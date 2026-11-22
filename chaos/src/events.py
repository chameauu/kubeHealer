import logging
import sys
from enum import Enum
from typing import Any

import structlog

from config import settings
from models import utc_now

_configured = False


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


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


def build_event(
    event_type: str,
    *,
    severity: Severity = Severity.INFO,
    component: str = "chaos.scenario",
    caused_by: str | None = None,
    payload: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build the event document the platform's consumers expect."""
    event: dict[str, Any] = {
        "event_type": event_type,
        "service": settings.service_name,
        "severity": severity.value,
        "component": component,
        "caused_by": caused_by,
        "timestamp": utc_now(),
        "payload": payload or {},
    }
    if reason is not None:
        event["reason"] = reason
    return event


def publish_event(
    event_type: str,
    *,
    severity: Severity = Severity.INFO,
    component: str = "chaos.scenario",
    caused_by: str | None = None,
    payload: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Emit one structured log line and return the event that was published."""
    event = build_event(
        event_type,
        severity=severity,
        component=component,
        caused_by=caused_by,
        payload=payload,
        reason=reason,
    )
    log.info(event_type, **event)
    return event
