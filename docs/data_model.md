# Data model

## Why MongoDB for cases

A case is read and written as one unit: parties, documents and status history belong together, and the shape grows as the programme adds features. One document per case avoids joining six tables on every read, and new optional fields don't need a migration. The audit log is a separate append-only collection.

## `cases` collection (schema version 2)

| Field | Type | Required | Rules |
|---|---|---|---|
| `case_number` | string | yes | `CIV-<year>-<6 digits>`; unique index |
| `schema_version` | int | yes | 2 for new records |
| `status` | enum | yes | `filed`, `triaged`, `fee_assessed`, `assigned`, `rejected` |
| `filer_type` | enum | yes | `citizen` or `lawyer` |
| `filer_email` | string | yes | email format |
| `bar_license_no` | string | lawyers only | checked at submission |
| `summary` | string | yes | 20 to 2,000 characters |
| `claim_amount` | number | yes | 0 or more; 0 for non-monetary claims |
| `parties[]` | array | yes | at least one `claimant` and one `respondent` |
| `parties[].national_id` | string | no | 13 digits when given |
| `documents[]` | array | yes | must include one `plaint` |
| `category`, `priority` | string | set by triage | |
| `fee` | number | set by fee worker | |
| `registrar_queue` | string | set by assignment worker | |
| `idempotency_key` | string | no | unique sparse index |
| `history[]` | array | yes | `{status, at, by}` for every change |

Indexes: `case_number` (unique), `idempotency_key` (unique, sparse), `status + priority` (registrar queues).

### Example
```json
{
  "case_number": "CIV-2026-000001",
  "schema_version": 2,
  "status": "assigned",
  "filer_type": "citizen",
  "filer_email": "filer@example.com",
  "summary": "Tenant has not paid rent for eight months and refuses to hand back possession of the flat.",
  "claim_amount": 480000,
  "parties": [
    {"name": "Sana Malik", "role": "claimant", "national_id": null},
    {"name": "Imran Qureshi", "role": "respondent", "national_id": null}
  ],
  "documents": [{"title": "Plaint", "kind": "plaint", "pages": 6}],
  "category": "property",
  "priority": "standard",
  "fee": 6300,
  "registrar_queue": "civil-registry-a",
  "history": [
    {"status": "filed", "at": "2026-10-08T16:40:01Z", "by": "efiling-api"},
    {"status": "triaged", "at": "2026-10-08T16:40:01Z", "by": "triage-worker"},
    {"status": "fee_assessed", "at": "2026-10-08T16:40:01Z", "by": "fee-worker"},
    {"status": "assigned", "at": "2026-10-08T16:40:01Z", "by": "assignment-worker"}
  ]
}
```

## Versioning and backwards compatibility

- **v1** stored the two sides as strings: `claimant_name`, `respondent_name`.
- **v2** replaced them with a `parties` list, so a case can have several claimants or respondents.
- Old records are not migrated in bulk. `upgrade_case()` in `app/store.py` converts them when read, so the API always returns v2. Test: `test_req09_old_v1_documents_still_readable`.
- Rule for future changes: add optional fields only; never rename or remove a field in place; bump `schema_version` and add an upgrade step.

## Event envelope (`efiling.events` stream)

| Field | Type | Required | Notes |
|---|---|---|---|
| `event_type` | enum | yes | `case.filed`, `case.triaged`, `case.fee_assessed`, `case.assigned` |
| `case_number` | string | yes | |
| `event_version` | int | yes | starts at 1; consumers ignore unknown fields |
| `occurred_at` | ISO 8601 | yes | UTC |
| `attempt` | int | yes | 1 on first delivery; up to 3 before dead-letter |
| `payload` | object | no | step results, e.g. `{"fee": 6300}` |

Events carry the case number and the step's result, not the whole case: workers read the current case from MongoDB, which keeps events small and avoids acting on stale data.

## Synchronous vs asynchronous

| Step | Mode | Why |
|---|---|---|
| Validate and save filing | Synchronous (HTTP request/response) | The filer needs the case number or the errors now |
| Triage, fee, assignment | Asynchronous (event-driven) | Can take longer, may call other systems later, must not block the filer |
| Notifications | Asynchronous | Email/SMS gateways are slow and can fail independently |
