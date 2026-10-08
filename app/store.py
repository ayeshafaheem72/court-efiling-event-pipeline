"""MongoDB storage for cases and the audit log.

Why a document database: a case is one nested record (parties, documents,
status history) that changes shape as the programme adds fields. Each case
is read and written as a whole, so one document per case fits better than
six joined tables.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from .models import SCHEMA_VERSION


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db(uri: Optional[str] = None):
    """Real MongoDB when MONGO_URI is set, otherwise an in-memory mock (demo and tests)."""
    uri = uri or os.getenv("MONGO_URI")
    if uri:
        from pymongo import MongoClient
        return MongoClient(uri)["efiling"]
    import mongomock
    return mongomock.MongoClient()["efiling"]


def upgrade_case(doc: dict) -> dict:
    """Read old documents without breaking them (REQ-09, backwards compatibility).

    v1 stored the two sides as plain strings: claimant_name / respondent_name.
    v2 stores a parties list so a case can have several claimants or respondents.
    """
    if doc is None:
        return doc
    version = doc.get("schema_version", 1)
    if version < 2:
        parties = []
        if doc.get("claimant_name"):
            parties.append({"name": doc.pop("claimant_name"), "role": "claimant", "national_id": None})
        if doc.get("respondent_name"):
            parties.append({"name": doc.pop("respondent_name"), "role": "respondent", "national_id": None})
        doc["parties"] = parties
        doc["schema_version"] = 2
    return doc


class CaseStore:
    def __init__(self, db):
        self.cases = db["cases"]
        self.audit = db["audit_log"]
        self.counters = db["counters"]
        self.cases.create_index([("case_number", ASCENDING)], unique=True)
        self.cases.create_index([("idempotency_key", ASCENDING)], unique=True, sparse=True)
        self.cases.create_index([("status", ASCENDING), ("priority", ASCENDING)])

    def next_case_number(self) -> str:
        year = datetime.now(timezone.utc).year
        seq = self.counters.find_one_and_update(
            {"_id": f"case-{year}"}, {"$inc": {"seq": 1}},
            upsert=True, return_document=ReturnDocument.AFTER)["seq"]
        return f"CIV-{year}-{seq:06d}"

    def find_by_idempotency_key(self, key: str) -> Optional[dict]:
        return upgrade_case(self.cases.find_one({"idempotency_key": key}, {"_id": 0}))

    def create(self, filing: dict, idempotency_key: Optional[str]) -> tuple[dict, bool]:
        """Insert a new case. Returns (case, created). A repeated idempotency key returns the first case."""
        if idempotency_key:
            existing = self.find_by_idempotency_key(idempotency_key)
            if existing:
                return existing, False
        case = {
            **filing,
            "case_number": self.next_case_number(),
            "status": "filed",
            "schema_version": SCHEMA_VERSION,
            "created_at": now(),
            "history": [{"status": "filed", "at": now(), "by": "efiling-api"}],
        }
        if idempotency_key:
            case["idempotency_key"] = idempotency_key
        try:
            self.cases.insert_one(dict(case))
        except DuplicateKeyError:  # two identical requests at the same moment
            return self.find_by_idempotency_key(idempotency_key), False
        self.log(case["case_number"], "case.created", {"filer_type": filing.get("filer_type")})
        case.pop("_id", None)
        return case, True

    def get(self, case_number: str) -> Optional[dict]:
        return upgrade_case(self.cases.find_one({"case_number": case_number}, {"_id": 0}))

    def update(self, case_number: str, fields: dict, status: str, by: str) -> None:
        self.cases.update_one(
            {"case_number": case_number},
            {"$set": {**fields, "status": status},
             "$push": {"history": {"status": status, "at": now(), "by": by}}})
        self.log(case_number, f"status.{status}", fields)

    def log(self, case_number: str, action: str, data: dict) -> None:
        """Append-only audit trail (REQ-10)."""
        self.audit.insert_one({"case_number": case_number, "action": action, "data": data, "at": now()})

    def registrar_queue_counts(self) -> list[dict]:
        """Aggregation example: open cases per registrar queue and priority."""
        return list(self.cases.aggregate([
            {"$match": {"status": "assigned"}},
            {"$group": {"_id": {"queue": "$registrar_queue", "priority": "$priority"}, "cases": {"$sum": 1}}},
            {"$sort": {"_id.queue": 1, "_id.priority": 1}},
        ]))
