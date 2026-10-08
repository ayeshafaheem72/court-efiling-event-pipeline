"""Run all workers against real MongoDB and Redis (used by docker compose)."""
import logging
import time

from .bus import EventBus, get_redis
from .store import CaseStore, get_db
from .workers import Workers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def main() -> None:
    workers = Workers(CaseStore(get_db()), EventBus(get_redis()))
    logging.info("workers started: %s", ", ".join(workers.groups()))
    while True:
        handled = sum(workers.bus.consume(g, f"{g}-1", h, block_ms=500) for g, h in workers.groups().items())
        if handled:
            logging.info("handled %d events", handled)
        else:
            time.sleep(0.2)


if __name__ == "__main__":
    main()
