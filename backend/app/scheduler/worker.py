"""Dedicated standalone APScheduler worker entrypoint for production deployments."""

import logging
import signal
import sys
import time

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import validate_production_settings
from app.scheduler.manager import create_scheduler

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging("DEBUG" if settings.app_debug else "INFO")
    validate_production_settings(settings)

    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Dedicated Otter Scheduler worker started with %d jobs", len(scheduler.get_jobs()))

    running = True

    def _handle_signal(_signum: int, _frame: object) -> None:
        nonlocal running
        logger.info("Shutdown signal received; stopping scheduler worker...")
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        while running:
            time.sleep(1.0)
    finally:
        scheduler.shutdown(wait=True)
        logger.info("Dedicated Otter Scheduler worker stopped cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    main()
