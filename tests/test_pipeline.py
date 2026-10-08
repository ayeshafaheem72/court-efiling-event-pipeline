"""Acceptance tests. Each test names the requirement it proves (see docs/traceability.csv)."""
import fakeredis
import mongomock
import pytest
from fastapi.testclient import TestClient

from app.bus import DEAD_LETTER, MAX_ATTEMPTS, EventBus
from app.main import create_app
from app.models import Event
from app.store import CaseStore
from app.workers import Workers, classify, fee_for
from scripts.republish import republish


@pytest.fixture
def env():
    store = CaseStore(mongomock.MongoClient()["efiling"])
    bus = EventBus(fakeredis.FakeRedis(decode_responses=True))
    return store, bus, TestClient(create_app(store, bus)), Workers(store, bus)


def filing(**over):
    body = {
        "filer_type": "citizen",
        "filer_email": "sana@example.com",
        "summary": "Tenant has not paid rent for eight months and refuses to leave the flat.",
        "claim_amount": 480000,
        "parties": [{"name": "Sana Malik", "role": "claimant"}, {"name": "Imran Qureshi", "role": "respondent"}],
        "documents": [{"title": "Plaint", "kind": "plaint", "pages": 6}],
    }
    body.update(over)
    return body


def test_req01_filing_returns_case_number_immediately(env):
    _, bus, client, _ = env
    r = client.post("/v1/cases", json=filing())
    assert r.status_code == 201
    assert r.json()["case_number"].startswith("CIV-")
    assert r.json()["status"] == "filed"
    assert bus.r.xlen("efiling.events") == 1  # follow-up work is queued, not done inline


def test_req02_incomplete_filing_rejected_with_field_errors(env):
    _, _, client, _ = env
    r = client.post("/v1/cases", json=filing(
        documents=[{"title": "Receipt", "kind": "evidence", "pages": 1}],
        parties=[{"name": "Sana Malik", "role": "claimant"}, {"name": "Ali", "role": "claimant"}]))
    assert r.status_code == 422
    fields = {d["field"] for d in r.json()["details"]}
    assert {"documents", "parties"} <= fields


def test_req03_lawyer_needs_bar_licence(env):
    _, _, client, _ = env
    r = client.post("/v1/cases", json=filing(filer_type="lawyer"))
    assert r.status_code == 422 and r.json()["code"] == "BAR_LICENSE_REQUIRED"
    assert client.post("/v1/cases", json=filing(filer_type="lawyer", bar_license_no="LHC-1")).status_code == 201


def test_req04_every_error_has_the_same_shape(env):
    _, _, client, _ = env
    for r in (client.get("/v1/cases/CIV-1900-000001"), client.post("/v1/cases", json={})):
        assert set(r.json()) == {"code", "message", "details"}


def test_req05_resubmitting_with_same_key_does_not_duplicate(env):
    store, bus, client, _ = env
    a = client.post("/v1/cases", json=filing(), headers={"Idempotency-Key": "k-1"})
    b = client.post("/v1/cases", json=filing(), headers={"Idempotency-Key": "k-1"})
    assert a.status_code == 201 and b.status_code == 200
    assert a.json()["case_number"] == b.json()["case_number"]
    assert store.cases.count_documents({}) == 1 and bus.r.xlen("efiling.events") == 1


def test_req06_events_carry_case_through_every_step(env):
    _, _, client, workers = env
    n = client.post("/v1/cases", json=filing()).json()["case_number"]
    workers.drain()
    case = client.get(f"/v1/cases/{n}").json()
    assert [h["status"] for h in case["history"]] == ["filed", "triaged", "fee_assessed", "assigned"]
    assert case["category"] == "property" and case["registrar_queue"] == "civil-registry-a"
    assert case["fee"] == fee_for("property", 480000)


def test_req07_failing_step_retries_then_goes_to_dead_letter(env):
    store, bus, client, workers = env
    calls = []

    def broken(event):
        if event.event_type != "case.triaged":
            return
        calls.append(event.attempt)
        raise RuntimeError("fee table unavailable")

    workers.fees = broken
    client.post("/v1/cases", json=filing())
    workers.drain()
    assert calls == list(range(1, MAX_ATTEMPTS + 1))
    dead = bus.dead_letters()
    assert len(dead) == 1 and dead[0]["group"] == "fees" and "fee table" in dead[0]["error"]
    assert store.get(dead[0]["event"]["case_number"])["status"] == "triaged"  # stopped safely, nothing lost


def test_req07_queue_outage_keeps_case_and_republishes(env):
    store, bus, client, workers = env

    class DownBus:
        def publish(self, _):
            raise ConnectionError("redis down")

    client.app.state.bus = DownBus()
    r = client.post("/v1/cases", json=filing())
    assert r.status_code == 503 and r.json()["code"] == "QUEUE_UNAVAILABLE"
    n = r.json()["details"][0]["case_number"]
    assert store.get(n)["status"] == "filed"
    assert republish(store, bus) == 1 and republish(store, bus) == 0
    workers.drain()
    assert store.get(n)["status"] == "assigned"


def test_req08_duplicate_event_is_processed_once(env):
    store, bus, client, workers = env
    n = client.post("/v1/cases", json=filing()).json()["case_number"]
    bus.publish(Event(event_type="case.filed", case_number=n))  # same event delivered twice
    workers.drain()
    statuses = [h["status"] for h in store.get(n)["history"]]
    assert statuses.count("triaged") == 1 and statuses.count("assigned") == 1


def test_req09_old_v1_documents_still_readable(env):
    store, _, client, _ = env
    store.cases.insert_one({"case_number": "CIV-2024-000009", "status": "assigned", "filer_type": "citizen",
                            "summary": "Old record created before parties became a list.", "claim_amount": 0,
                            "claimant_name": "Ahmed", "respondent_name": "Bilal", "history": []})
    case = client.get("/v1/cases/CIV-2024-000009").json()
    assert case["schema_version"] == 2
    assert [(p["name"], p["role"]) for p in case["parties"]] == [("Ahmed", "claimant"), ("Bilal", "respondent")]


def test_req10_every_status_change_is_audited(env):
    store, _, client, workers = env
    n = client.post("/v1/cases", json=filing()).json()["case_number"]
    workers.drain()
    actions = [a["action"] for a in store.audit.find({"case_number": n})]
    assert actions == ["case.created", "status.triaged", "status.fee_assessed", "status.assigned"]


def test_req11_filer_notified_once_per_milestone(env):
    store, bus, client, workers = env
    n = client.post("/v1/cases", json=filing()).json()["case_number"]
    workers.drain()
    bus.publish(Event(event_type="case.assigned", case_number=n))  # replay
    workers.drain()
    notes = list(store.cases.database["notifications"].find({}))
    assert len(notes) == 3 and all(x["to"] == "sana@example.com" for x in notes)


@pytest.mark.parametrize("summary,expected", [
    ("Urgent injunction to stop demolition of our house on the disputed plot", ("property", "urgent")),
    ("Employer has not paid wages since March", ("labour", "standard")),
    ("Custody of my daughter after divorce", ("family", "standard")),
    ("Neighbour's dog keeps barking at night", ("general", "standard")),
])
def test_req12_triage_rules(summary, expected):
    assert classify(summary) == expected


def test_req12_registry_sees_queue_counts(env):
    _, _, client, workers = env
    client.post("/v1/cases", json=filing())
    client.post("/v1/cases", json=filing(summary="Urgent stay order needed against eviction from my shop premises."))
    workers.drain()
    rows = client.get("/v1/registry/queues").json()
    assert {(r["queue"], r["priority"], r["cases"]) for r in rows} == {
        ("civil-registry-a", "standard", 1), ("civil-registry-a", "urgent", 1)}
