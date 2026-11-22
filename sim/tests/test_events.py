"""The event envelope every service emits (one JSON line per event)."""

from events import Causality, Event, Severity, publish_event


def test_event_carries_required_fields() -> None:
    event = publish_event(
        "sim.lifecycle.transition",
        severity=Severity.INFO,
        component="sim.volume",
        payload={"resource_id": "vol-abc123", "state": "available"},
    )

    assert isinstance(event, Event)
    assert event.event_type == "sim.lifecycle.transition"
    assert event.service == "sim"
    assert event.severity == Severity.INFO
    assert event.component == "sim.volume"
    assert event.payload == {"resource_id": "vol-abc123", "state": "available"}
    assert event.causality is None
    assert "T" in event.timestamp


def test_event_records_causality_when_given() -> None:
    event = publish_event(
        "sim.lifecycle.error",
        severity=Severity.MEDIUM,
        component="sim.instance",
        payload={"resource_id": "inst-xyz"},
        caused_by="fault-789",
        reason="transient_error fault during provision",
    )

    assert event.causality == Causality(
        caused_by="fault-789", reason="transient_error fault during provision"
    )


def test_event_defaults_payload_to_empty_dict() -> None:
    event = publish_event("sim.fault.triggered", severity=Severity.LOW, component="sim.subnet")

    assert event.payload == {}
