"""Re-send case.filed for cases saved while the queue was down (REQ-07).

The API logs `event.publish_failed` in the audit log when Redis is unreachable.
Run this on a schedule; it is safe to run twice because workers skip finished steps.
"""
from app.bus import EventBus, get_redis
from app.models import Event
from app.store import CaseStore, get_db


def republish(store: CaseStore, bus: EventBus) -> int:
    sent = 0
    for entry in store.audit.find({"action": "event.publish_failed", "resolved": {"$ne": True}}):
        case = store.get(entry["case_number"])
        if case and case["status"] == "filed":
            bus.publish(Event(event_type="case.filed", case_number=case["case_number"]))
            sent += 1
        store.audit.update_one({"_id": entry["_id"]}, {"$set": {"resolved": True}})
    return sent


if __name__ == "__main__":
    print(f"re-sent {republish(CaseStore(get_db()), EventBus(get_redis()))} events")
