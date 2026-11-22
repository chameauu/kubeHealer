"""Chaos Mesh manifest contract: one CR per fault type, pure functions only.

Rows 45-53 of the M2 behaviour list. Nothing here talks to a cluster: the
manifest builder and the status summariser are pure, so the shape of what we
would apply is asserted directly.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from chaos_mesh import (
    NetworkLatencyParams,
    PacketLossParams,
    PodKillParams,
    build_manifest,
    summarize_status,
)
from models import K8sTarget, Scenario

NAMESPACE = "argus-system"
SCENARIO_ID = "scn-0123456789"


def _target(**overrides: Any) -> K8sTarget:
    values: dict[str, Any] = {
        "namespace": NAMESPACE,
        "label_selector": {"app": "orders"},
        "mode": "one",
    }
    values.update(overrides)
    return K8sTarget(**values)


def _scenario(
    fault_type: str = "pod_kill",
    *,
    params: dict[str, Any] | None = None,
    target: K8sTarget | None = None,
    duration_seconds: float | None = 30.0,
) -> Scenario:
    return Scenario(
        id=SCENARIO_ID,
        name="visual-check",
        correlation_id="corr-abc",
        domain="chaos_mesh",
        fault_type=fault_type,
        target=_target() if target is None else target,
        duration_seconds=duration_seconds,
        params={} if params is None else params,
    )


def _spec(fault_type: str = "pod_kill", **kwargs: Any) -> dict[str, Any]:
    return build_manifest(_scenario(fault_type, **kwargs))["spec"]


def test_pod_kill_manifest_shape() -> None:
    manifest = build_manifest(_scenario("pod_kill"))

    assert manifest["apiVersion"] == "chaos-mesh.org/v1alpha1"
    assert manifest["kind"] == "PodChaos"
    spec = manifest["spec"]
    assert spec["action"] == "pod-kill"
    assert spec["selector"]["namespaces"] == [NAMESPACE]
    assert spec["selector"]["labelSelectors"] == {"app": "orders"}
    assert spec["mode"] == "one"
    assert spec["duration"] == "30s"


def test_grace_period_and_duration_are_optional() -> None:
    default_spec = _spec("pod_kill")
    configured_spec = _spec("pod_kill", params={"grace_period_seconds": 15})
    timeless_spec = _spec("pod_kill", duration_seconds=None)

    assert "gracePeriod" not in default_spec
    assert configured_spec["gracePeriod"] == 15
    assert "duration" not in timeless_spec


def test_metadata_names_labels_and_namespace() -> None:
    manifest = build_manifest(_scenario("pod_kill"))
    metadata = manifest["metadata"]

    assert metadata["name"] == "kubeheal-chaos-" + SCENARIO_ID
    assert metadata["namespace"] == NAMESPACE
    assert metadata["labels"] == {
        "app.kubernetes.io/managed-by": "kubeheal-chaos",
        "kubeheal.io/scenario-id": SCENARIO_ID,
    }


def test_network_latency_manifest_shape() -> None:
    spec = _spec(
        "network_latency",
        params={"latency": "200ms", "jitter": "50ms", "correlation": "25"},
    )

    assert spec["action"] == "delay"
    assert spec["delay"] == {"latency": "200ms", "jitter": "50ms", "correlation": "25"}
    assert "loss" not in spec


def test_packet_loss_manifest_shape() -> None:
    spec = _spec("packet_loss", params={"loss": "33"})

    assert spec["action"] == "loss"
    assert spec["loss"] == {"loss": "33", "correlation": "0"}
    assert "delay" not in spec


def test_loss_is_accepted_as_the_crd_field_name() -> None:
    params = PacketLossParams.model_validate({"loss": "33"})

    assert params.loss_percent == "33"


def test_io_delay_manifest_shape() -> None:
    spec = _spec(
        "io_delay",
        params={"delay": "90ms", "percent": 50, "path": "/data"},
        duration_seconds=90,
    )

    assert spec["action"] == "latency"
    assert spec["delay"] == "90ms"
    assert spec["percent"] == 50
    assert spec["path"] == "/data"
    assert "volumePath" in spec
    assert spec["duration"] == "90s"


@pytest.mark.parametrize("mode", ["one", "all"])
def test_modes_without_value_emit_mode_only(mode: str) -> None:
    spec = _spec("pod_kill", target=_target(mode=mode))

    assert spec["mode"] == mode
    assert spec.get("value") is None


def test_fixed_percent_carries_mode_and_value() -> None:
    spec = _spec("pod_kill", target=_target(mode="fixed-percent", value="50"))

    assert spec["mode"] == "fixed-percent"
    assert spec["value"] == "50"


@pytest.mark.parametrize("mode", ["fixed", "fixed-percent", "random-max-percent"])
def test_value_requiring_modes_reject_a_missing_value(mode: str) -> None:
    with pytest.raises(ValidationError, match="target.value is required"):
        build_manifest(_scenario("pod_kill", target=K8sTarget(namespace=NAMESPACE, mode=mode)))


def test_unknown_param_keys_are_ignored() -> None:
    params = PodKillParams(grace_period_seconds=3, unexpected_key="ignored")

    assert not hasattr(params, "unexpected_key")
    assert params.grace_period_seconds == 3


def test_io_delay_percent_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_manifest(_scenario("io_delay", params={"percent": 150}))


def test_defaults_are_mild() -> None:
    assert NetworkLatencyParams().latency == "100ms"
    assert PodKillParams().grace_period_seconds == 0


def test_summarize_status() -> None:
    assert summarize_status(None) is None
    assert summarize_status({}) is None

    summary = summarize_status(
        {
            "experiment": {"phase": "Running"},
            "conditions": [{"type": "Injected", "status": "True"}],
            "instances": ["orders-6f7c9-abcde"],
        }
    )

    assert summary == {
        "experiment": {"phase": "Running"},
        "conditions": [{"type": "Injected", "status": "True"}],
    }
