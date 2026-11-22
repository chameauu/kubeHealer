import logging
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field

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


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Causality(BaseModel):
    """Which rule, decision or upstream event caused this one."""

    caused_by: str
    reason: str | None = None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Event(BaseModel):
    """The event envelope every service emits."""

    event_type: str
    service: str = "sim"
    severity: Severity
    component: str
    causality: Causality | None = None
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)


def publish_event(
    event_type: str,
    *,
    severity: Severity,
    component: str,
    payload: dict[str, Any] | None = None,
    caused_by: str | None = None,
    reason: str | None = None,
) -> Event:
    """Build an event, emit it as one JSON line, and return it."""
    causality = Causality(caused_by=caused_by, reason=reason) if caused_by is not None else None
    event = Event(
        event_type=event_type,
        service=settings.service_name,
        severity=severity,
        component=component,
        causality=causality,
        timestamp=_timestamp(),
        payload=payload or {},
    )
    log_event(
        event.event_type,
        severity=event.severity.value,
        component=event.component,
        payload=event.payload,
        causality=causality.model_dump() if causality is not None else None,
    )
    return event
