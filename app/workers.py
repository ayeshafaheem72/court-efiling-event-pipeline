"""Workers that react to events. Each one is its own consumer group.

    case.filed        -> triage worker      -> case.triaged
    case.triaged      -> fee worker         -> case.fee_assessed
    case.fee_assessed -> assignment worker  -> case.assigned
    every event       -> notification worker (writes to an outbox)

Delivery is at-least-once, so every worker checks the case status first and
does nothing if the step is already done (REQ-08, idempotent consumers).
"""
from __future__ import annotations

import re

from .bus import EventBus
from .models import Event
from .store import CaseStore

CATEGORY_RULES = [
    ("family", r"\b(divorce|custody|maintenance|khula|guardian)\b"),
    ("property", r"\b(land|plot|property|tenant|rent|eviction|possession)\b"),
    ("labour", r"\b(salary|wages|termination|employer|employee|gratuity)\b"),
    ("commercial", r"\b(contract|invoice|supplier|partnership|cheque|payment)\b"),
]
URGENT = r"\b(injunction|stay order|eviction|urgent|demolition)\b"

FEE_RULES = {  # base fee + share of claim, capped (local currency)
    "family": (500, 0.0, 500),
    "property": (1500, 0.01, 50000),
    "labour": (0, 0.0, 0),          # labour claims are free to file
    "commercial": (2000, 0.015, 100000),
    "general": (1000, 0.01, 30000),
}
REGISTRAR_QUEUES = {"family": "family-registry", "property": "civil-registry-a",
                    "commercial": "commercial-registry", "labour": "labour-registry",
                    "general": "civil-registry-b"}


def classify(summary: str) -> tuple[str, str]:
    text = summary.lower()
    category = next((name for name, rx in CATEGORY_RULES if re.search(rx, text)), "general")
    priority = "urgent" if re.search(URGENT, text) else "standard"
    return category, priority


def fee_for(category: str, claim_amount: float) -> float:
    base, share, cap = FEE_RULES[category]
    return float(min(base + share * claim_amount, cap)) if cap else 0.0


class Workers:
    def __init__(self, store: CaseStore, bus: EventBus):
        self.store, self.bus = store, bus

    def triage(self, event: Event) -> None:
        if event.event_type != "case.filed":
            return
        case = self.store.get(event.case_number)
        if not case or case["status"] != "filed":
            return
        category, priority = classify(case["summary"])
        self.store.update(case["case_number"], {"category": category, "priority": priority},
                          "triaged", by="triage-worker")
        self.bus.publish(Event(event_type="case.triaged", case_number=case["case_number"],
                               payload={"category": category, "priority": priority}))

    def fees(self, event: Event) -> None:
        if event.event_type != "case.triaged":
            return
        case = self.store.get(event.case_number)
        if not case or case["status"] != "triaged":
            return
        fee = fee_for(case["category"], case["claim_amount"])
        self.store.update(case["case_number"], {"fee": fee}, "fee_assessed", by="fee-worker")
        self.bus.publish(Event(event_type="case.fee_assessed", case_number=case["case_number"],
                               payload={"fee": fee}))

    def assign(self, event: Event) -> None:
        if event.event_type != "case.fee_assessed":
            return
        case = self.store.get(event.case_number)
        if not case or case["status"] != "fee_assessed":
            return
        queue = REGISTRAR_QUEUES[case["category"]]
        self.store.update(case["case_number"], {"registrar_queue": queue}, "assigned", by="assignment-worker")
        self.bus.publish(Event(event_type="case.assigned", case_number=case["case_number"],
                               payload={"registrar_queue": queue, "priority": case["priority"]}))

    def notify(self, event: Event) -> None:
        case = self.store.get(event.case_number)
        if not case:
            return
        messages = {
            "case.filed": f"Case {case['case_number']} received. We will confirm the fee shortly.",
            "case.fee_assessed": f"Filing fee for {case['case_number']}: {case.get('fee', 0):,.0f}.",
            "case.assigned": f"{case['case_number']} is with the registry ({case.get('priority')} priority).",
        }
        if event.event_type in messages:
            outbox = self.store.cases.database["notifications"]
            key = f"{event.case_number}:{event.event_type}"
            outbox.update_one({"_id": key},
                              {"$setOnInsert": {"to": case["filer_email"], "text": messages[event.event_type]}},
                              upsert=True)

    def groups(self):
        return {"triage": self.triage, "fees": self.fees, "assignment": self.assign,
                "notifications": self.notify}

    def drain(self, max_rounds: int = 20) -> int:
        """Run every worker until the stream is quiet. Used by the demo and tests."""
        total = 0
        for _ in range(max_rounds):
            handled = sum(self.bus.consume(g, f"{g}-1", h) for g, h in self.groups().items())
            total += handled
            if handled == 0:
                break
        return total
