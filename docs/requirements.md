# Requirements: civil case e-filing

The scenario is a fictional national court service moving civil case filing from a paper counter to an online service. No real court's data or rules are used; fees and categories are illustrative.

## 1. Business need

Filing a civil case today means travelling to the court, queuing at a counter, being sent home if one document is missing, paying the fee at a bank branch, and coming back to check progress. Registrars sort paper files by hand, so urgent matters (evictions, demolitions, injunctions) wait in the same pile as everything else.

**Goal:** a filer gets a case number, a fee and a status without visiting the court, and registrars receive cases already sorted by category and urgency.

**Measures of success**
| Measure | As-is (to be baselined) | Target |
|---|---|---|
| Visits to court before a case is registered | 2 or more | 0 |
| Time from submission to case number | Same day at best | Under 2 seconds |
| Filings rejected after the filer has left | Not tracked | 0 (all checks happen at submission) |
| Urgent cases identified at intake | Not tracked | 100% of filings screened |

## 2. Stakeholders

| Stakeholder | Interest |
|---|---|
| Citizens filing their own cases | Fewer trips, a clear answer straight away |
| Lawyers | Bulk and repeat filing, bar licence checked once |
| Filing counter clerks | Fewer incomplete files; move to exception handling |
| Registrars | Pre-sorted queues, urgent cases first |
| Court finance | Correct fees, fewer manual calculations |
| IT and security | Audit trail, no lost cases when a component fails |

## 3. Process

See the swimlane maps: [as-is](process/as_is.svg) and [to-be](process/to_be.svg). The `.bpmn` files open in bpmn.io or Camunda Modeler.

Key changes from as-is to to-be:
1. Completeness checks move from the clerk to the API, at the moment of submission (synchronous).
2. Triage, fee assessment, assignment and notifications move to workers on a message queue (asynchronous), so the filer never waits for them.
3. The physical file hand-off becomes an event (`case.assigned`).

## 4. Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| REQ-01 | The service returns a case number as soon as a complete filing is submitted. | Must |
| REQ-02 | An incomplete filing is rejected at submission with an error for each field (a plaint and at least one claimant and one respondent are mandatory). | Must |
| REQ-03 | A lawyer must give a bar licence number. | Must |
| REQ-04 | Every error response has the same shape: `code`, `message`, `details`. | Must |
| REQ-05 | Submitting the same filing twice with the same `Idempotency-Key` returns the first case and does not create a duplicate. | Must |
| REQ-06 | After filing, the case moves through triage, fee assessment and registrar assignment without manual hand-offs. | Must |
| REQ-07 | No case is lost when a step fails: failed steps retry up to 3 times and then go to a dead-letter queue; if the queue is down, the case is still saved and its event is re-sent later. | Must |
| REQ-08 | A step that receives the same event twice does the work once. | Must |
| REQ-09 | Records saved under an older data schema can still be read. | Should |
| REQ-10 | Every status change is written to an append-only audit log. | Must |
| REQ-11 | The filer is notified once when the case is received, when the fee is set and when it reaches the registry. | Should |
| REQ-12 | Cases are categorised and urgent matters flagged at intake; registrars can see open cases per queue and priority. | Must |

## 5. User stories and acceptance criteria

**US-01 File a case online** (REQ-01, REQ-02, REQ-04)
As a citizen, I want to file my case online so that I get a case number without visiting the court.
- Given a filing with a plaint, a claimant and a respondent, when I submit it, then I receive a case number and status `filed`.
- Given a filing without a plaint, when I submit it, then I receive a 422 error naming the `documents` field, and no case is created.

**US-02 Lawyer filing** (REQ-03)
As a lawyer, I want my licence checked at filing so that the registry doesn't return my file later.
- Given I select filer type `lawyer` and leave the licence blank, when I submit, then I get error `BAR_LICENSE_REQUIRED`.

**US-03 Safe resubmission** (REQ-05)
As a filer on a weak connection, I want to press submit again without creating two cases.
- Given I resend a filing with the same Idempotency-Key, then I get the same case number with status 200.

**US-04 Automatic follow-up** (REQ-06, REQ-11, REQ-12)
As a filer, I want to receive my fee and the case's progress by email.
- Given my case is filed, when the workers run, then the case reaches `assigned` with a category, fee and registrar queue, and I receive three notifications.

**US-05 Registrar queue** (REQ-12)
As a registrar, I want urgent cases flagged so that I handle them first.
- Given a filing that mentions an injunction, eviction or demolition, then its priority is `urgent`.

**US-06 Nothing lost** (REQ-07, REQ-08, REQ-10)
As the court's IT lead, I want failed steps parked, not dropped, and every change audited.
- Given the fee step fails 3 times, then the event is in the dead-letter queue and the case stays at `triaged`.
- Given the queue is down at filing, then the filer gets a 503 with their case number, and the event is re-sent when the republish job runs.

## 6. Non-functional requirements

| ID | Area | Requirement |
|---|---|---|
| NFR-01 | Performance | Submission responds in under 2 seconds at the 95th percentile; follow-up steps run asynchronously. |
| NFR-02 | Reliability | At-least-once delivery between steps; idempotent workers; dead-letter queue for failures. |
| NFR-03 | Auditability | Append-only audit log of every status change, kept with the case number. |
| NFR-04 | Compatibility | Schema changes are additive; readers upgrade older records (see [data model](data_model.md)). |
| NFR-05 | Privacy | National ID is optional and validated (13 digits); only what is needed for the filing is stored. |
| NFR-06 | Scalability | Workers scale horizontally: each worker type is a consumer group, so more instances share the load. |

## 7. External interfaces

| Interface | Direction | Contract |
|---|---|---|
| E-filing API | Portal and mobile app to service | [docs/openapi.json](openapi.json) (OpenAPI 3.1) |
| Event stream `efiling.events` | Service to workers | Event envelope in [data model](data_model.md) |
| Notifications outbox | Workers to email/SMS gateway | `notifications` collection, one record per case and milestone |

## 8. Out of scope

Online fee payment, hearing scheduling, document storage and identity verification (each would be its own service that listens to these events).

## 9. Open questions (what I would ask in discovery)

1. Who owns the fee table, and how often does it change? (Decides whether fees live in config or a service.)
2. Are any case types filed on paper only by law?
3. What does "urgent" mean legally for each court, and who can override it?
4. How long must audit records be kept?
5. Which notification channels are allowed: email, SMS, in-app?
