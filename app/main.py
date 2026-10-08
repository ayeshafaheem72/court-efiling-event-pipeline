"""E-filing API.

POST /v1/cases is synchronous for the part the filer needs straight away
(validation and a case number) and asynchronous for everything else
(triage, fees, assignment, notifications), which happens on the queue.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .bus import EventBus, get_redis
from .models import CaseFilingRequest, CaseView, ErrorBody, Event, FilingReceipt
from .store import CaseStore, get_db


def create_app(store: Optional[CaseStore] = None, bus: Optional[EventBus] = None) -> FastAPI:
    app = FastAPI(
        title="Court E-Filing API",
        version="1.0.0",
        description="Accepts civil case filings and hands follow-up work to event-driven workers.",
    )
    app.state.store = store or CaseStore(get_db())
    app.state.bus = bus or EventBus(get_redis())

    def error(status: int, code: str, message: str, details: Optional[list] = None) -> JSONResponse:
        return JSONResponse(status_code=status,
                            content=ErrorBody(code=code, message=message, details=details or []).model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "problem": e["msg"]} for e in exc.errors()]
        return error(422, "VALIDATION_FAILED", "The filing is incomplete or has invalid fields.", details)

    @app.post("/v1/cases", status_code=201, response_model=FilingReceipt,
              responses={200: {"model": FilingReceipt, "description": "Same Idempotency-Key sent again"},
                         422: {"model": ErrorBody}, 503: {"model": ErrorBody}})
    def file_case(body: CaseFilingRequest, response: Response,
                  idempotency_key: Optional[str] = Header(default=None, max_length=64)):
        if body.filer_type == "lawyer" and not body.bar_license_no:
            return error(422, "BAR_LICENSE_REQUIRED", "Lawyers must give a bar licence number.",
                         [{"field": "bar_license_no", "problem": "required for lawyers"}])
        case, created = app.state.store.create(body.model_dump(mode="json"), idempotency_key)
        if created:
            try:
                app.state.bus.publish(Event(event_type="case.filed", case_number=case["case_number"]))
            except Exception:
                # Case is saved; the outbox sweep (scripts/republish.py) re-sends missing events.
                app.state.store.log(case["case_number"], "event.publish_failed", {})
                return error(503, "QUEUE_UNAVAILABLE",
                             "Case saved but follow-up is delayed. Keep your case number.",
                             [{"case_number": case["case_number"]}])
        else:
            response.status_code = 200
        return FilingReceipt(case_number=case["case_number"], status=case["status"],
                             status_url=f"/v1/cases/{case['case_number']}")

    @app.get("/v1/cases/{case_number}", response_model=CaseView, responses={404: {"model": ErrorBody}})
    def get_case(case_number: str):
        case = app.state.store.get(case_number)
        if not case:
            return error(404, "CASE_NOT_FOUND", f"No case {case_number}.")
        return CaseView(**case)

    @app.get("/v1/registry/queues")
    def queue_counts():
        return [{"queue": r["_id"]["queue"], "priority": r["_id"]["priority"], "cases": r["cases"]}
                for r in app.state.store.registrar_queue_counts()]

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
