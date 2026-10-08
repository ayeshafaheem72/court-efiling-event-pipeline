"""Message queue on Redis Streams.

The API publishes an event and returns straight away; workers pick events up
in their own time. Each worker reads through a consumer group, so a message
is handled by one worker instance and stays pending until it is acknowledged.
A message that keeps failing is retried, then parked on a dead-letter stream
for a person to look at (REQ-07).
"""
from __future__ import annotations

import json
import os
from typing import Callable, Optional

from .models import Event

STREAM = "efiling.events"
DEAD_LETTER = "efiling.events.dlq"
MAX_ATTEMPTS = 3


def get_redis(url: Optional[str] = None):
    """Real Redis when REDIS_URL is set, otherwise an in-process fake (demo and tests)."""
    url = url or os.getenv("REDIS_URL")
    if url:
        import redis
        return redis.Redis.from_url(url, decode_responses=True)
    import fakeredis
    return fakeredis.FakeRedis(decode_responses=True)


class EventBus:
    def __init__(self, r):
        self.r = r

    def publish(self, event: Event) -> str:
        return self.r.xadd(STREAM, {"event": event.model_dump_json()})

    def ensure_group(self, group: str) -> None:
        try:
            self.r.xgroup_create(STREAM, group, id="0", mkstream=True)
        except Exception as e:  # group already exists
            if "BUSYGROUP" not in str(e):
                raise

    def consume(self, group: str, consumer: str, handler: Callable[[Event], None],
                count: int = 10, block_ms: Optional[int] = None) -> int:
        """Read up to `count` new events for this group and handle them. Returns how many were read."""
        self.ensure_group(group)
        resp = self.r.xreadgroup(group, consumer, {STREAM: ">"}, count=count, block=block_ms)
        handled = 0
        for _stream, messages in resp or []:
            for msg_id, fields in messages:
                event = Event.model_validate_json(fields["event"])
                retry_for = event.payload.get("retry_for")
                if retry_for and retry_for != group:  # a retry meant for another worker
                    self.r.xack(STREAM, group, msg_id)
                    continue
                try:
                    handler(event)
                except Exception as exc:
                    self._retry_or_dead_letter(event, group, exc)
                self.r.xack(STREAM, group, msg_id)
                handled += 1
        return handled

    def _retry_or_dead_letter(self, event: Event, group: str, exc: Exception) -> None:
        if event.attempt >= MAX_ATTEMPTS:
            self.r.xadd(DEAD_LETTER, {"event": event.model_dump_json(), "group": group, "error": str(exc)})
            return
        retry = event.model_copy(update={"attempt": event.attempt + 1,
                                         "payload": {**event.payload, "retry_for": group}})
        self.publish(retry)

    def dead_letters(self) -> list[dict]:
        return [{"id": i, **{k: (json.loads(v) if k == "event" else v) for k, v in f.items()}}
                for i, f in self.r.xrange(DEAD_LETTER)]
