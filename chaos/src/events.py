import logging
import sys

import structlog

from config import settings

_configured = False


def configure_logging(level: str | None = None) -> None:
    global _configured
    if _configured:
        return
    level = (level or settings.log_level).upper()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


log = structlog.get_logger()


def log_event(event_type: str, **fields: object) -> None:
    """One event = one JSON line. No message bus."""
    log.info(event_type, event_type=event_type, service=settings.service_name, **fields)
