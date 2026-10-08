"""End-to-end demo with no setup: in-memory MongoDB and Redis.

    python -m scripts.demo

Set MONGO_URI and REDIS_URL to run the same flow against real services.
"""
import time

from fastapi.testclient import TestClient

from app.bus import EventBus, get_redis
from app.main import create_app
from app.store import CaseStore, get_db
from app.workers import Workers

SAMPLES = [
    ("citizen", "Tenant has not paid rent for eight months and refuses to hand back possession of the flat.", 480000),
    ("lawyer", "Supplier delivered goods under a signed contract and the buyer's cheque bounced; invoice unpaid.", 2500000),
    ("citizen", "Employer withheld three months of salary and gratuity after termination without notice.", 210000),
    ("lawyer", "Urgent injunction sought to stop demolition of a family-owned property during a land dispute.", 0),
    ("citizen", "Petition for custody of two children and monthly maintenance after separation.", 0),
]


def body(filer_type: str, summary: str, amount: float) -> dict:
    return {
        "filer_type": filer_type,
        "filer_email": "filer@example.com",
        "bar_license_no": "LHC-12345" if filer_type == "lawyer" else None,
        "summary": summary,
        "claim_amount": amount,
        "parties": [{"name": "Sana Malik", "role": "claimant"}, {"name": "Imran Qureshi", "role": "respondent"}],
        "documents": [{"title": "Plaint", "kind": "plaint", "pages": 6}],
    }


def main() -> None:
    store, bus = CaseStore(get_db()), EventBus(get_redis())
    client = TestClient(create_app(store, bus))
    workers = Workers(store, bus)

    numbers = []
    for filer, summary, amount in SAMPLES:
        t0 = time.perf_counter()
        r = client.post("/v1/cases", json=body(filer, summary, amount))
        ms = (time.perf_counter() - t0) * 1000
        numbers.append(r.json()["case_number"])
        print(f"POST /v1/cases -> {r.status_code} {r.json()['case_number']} ({ms:.0f} ms, filer waits only for this)")

    deliveries = workers.drain()
    print(f"\n{bus.r.xlen('efiling.events')} events on the queue, {deliveries} deliveries across {len(workers.groups())} workers\n")
    print(f"{'case':<17}{'category':<12}{'priority':<10}{'fee':>9}  queue")
    for n in numbers:
        c = client.get(f"/v1/cases/{n}").json()
        print(f"{n:<17}{c['category']:<12}{c['priority']:<10}{c['fee']:>9,.0f}  {c['registrar_queue']}")

    print("\nregistry queues:", client.get("/v1/registry/queues").json())
    print("notifications sent:", store.cases.database["notifications"].count_documents({}))
    print("history of", numbers[0], [h["status"] for h in client.get(f"/v1/cases/{numbers[0]}").json()["history"]])


if __name__ == "__main__":
    main()
