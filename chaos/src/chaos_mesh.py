"""Chaos Mesh: manifests and the k8s API client.

Manifest construction is pure: given a scenario it returns the exact
`chaos-mesh.org/v1alpha1` custom resource that would be applied. The client
performs the create/get/delete calls against the cluster's CustomObjectsApi.

Fault types map one-to-one onto CRD kinds:

    pod_kill        -> PodChaos     action=pod-kill
    network_latency -> NetworkChaos action=delay   (delay block)
    packet_loss     -> NetworkChaos action=loss    (loss block)
    io_delay        -> IOChaos      action=latency
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from models import ChaosMeshFaultType, K8sTarget, Scenario

GROUP = "chaos-mesh.org"
VERSION = "v1alpha1"

_PLURAL: dict[ChaosMeshFaultType, str] = {
    ChaosMeshFaultType.POD_KILL: "podchaos",
    ChaosMeshFaultType.NETWORK_LATENCY: "networkchaos",
    ChaosMeshFaultType.PACKET_LOSS: "networkchaos",
    ChaosMeshFaultType.IO_DELAY: "iochaos",
}

_KIND: dict[ChaosMeshFaultType, str] = {
    ChaosMeshFaultType.POD_KILL: "PodChaos",
    ChaosMeshFaultType.NETWORK_LATENCY: "NetworkChaos",
    ChaosMeshFaultType.PACKET_LOSS: "NetworkChaos",
    ChaosMeshFaultType.IO_DELAY: "IOChaos",
}

_PLURAL_BY_KIND = {kind: _PLURAL[fault] for fault, kind in _KIND.items()}


class PodKillParams(BaseModel):
    """`gracePeriod` is how long the kubelet waits before removing the pod."""

    model_config = ConfigDict(extra="ignore")

    grace_period_seconds: int = Field(default=0, ge=0, le=600)


class NetworkLatencyParams(BaseModel):
    """Delay applied to matching traffic; durations are Go duration strings."""

    model_config = ConfigDict(extra="ignore")

    latency: str = "100ms"
    jitter: str = "0ms"
    correlation: str = "0"


class PacketLossParams(BaseModel):
    """`loss` is the CRD field name; the model exposes it as `loss_percent`."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    loss_percent: str = Field(default="25", alias="loss")
    correlation: str = "0"


class IODelayParams(BaseModel):
    """IOChaos latency applied to a fraction of operations under `path`."""

    model_config = ConfigDict(extra="ignore")

    delay: str = "100ms"
    percent: int = Field(default=100, ge=0, le=100)
    path: str = "/*"
    volume_path: str = "/"


_PARAMS_MODEL: dict[ChaosMeshFaultType, type[BaseModel]] = {
    ChaosMeshFaultType.POD_KILL: PodKillParams,
    ChaosMeshFaultType.NETWORK_LATENCY: NetworkLatencyParams,
    ChaosMeshFaultType.PACKET_LOSS: PacketLossParams,
    ChaosMeshFaultType.IO_DELAY: IODelayParams,
}


def parse_params(fault_type: ChaosMeshFaultType | str, raw: dict[str, Any] | None) -> BaseModel:
    """Validate free-form params against the chosen fault type's shape.

    Raises pydantic.ValidationError for anything malformed so the engine can
    reject the request before recording anything.
    """
    return _PARAMS_MODEL[ChaosMeshFaultType(fault_type)].model_validate(raw or {})


def _selector(target: K8sTarget) -> dict[str, Any]:
    selector: dict[str, Any] = {"namespaces": [target.namespace]}
    if target.label_selector:
        selector["labelSelectors"] = dict(target.label_selector)
    return selector


def _mode_fields(target: K8sTarget) -> dict[str, Any]:
    fields: dict[str, Any] = {"mode": target.mode}
    if target.value is not None:
        fields["value"] = target.value
    return fields


def _duration(duration_seconds: float | None) -> str | None:
    if duration_seconds is None:
        return None
    return f"{int(duration_seconds)}s"


def _with_duration(spec: dict[str, Any], duration_seconds: float | None) -> dict[str, Any]:
    duration = _duration(duration_seconds)
    if duration is not None:
        spec["duration"] = duration
    return spec


def _spec_pod_kill(
    target: K8sTarget, duration_seconds: float | None, params: PodKillParams
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "action": "pod-kill",
        "selector": _selector(target),
        **_mode_fields(target),
    }
    if params.grace_period_seconds:
        spec["gracePeriod"] = params.grace_period_seconds
    return _with_duration(spec, duration_seconds)


def _spec_network_latency(
    target: K8sTarget, duration_seconds: float | None, params: NetworkLatencyParams
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "action": "delay",
        "selector": _selector(target),
        **_mode_fields(target),
        "delay": {
            "latency": params.latency,
            "jitter": params.jitter,
            "correlation": params.correlation,
        },
    }
    return _with_duration(spec, duration_seconds)


def _spec_packet_loss(
    target: K8sTarget, duration_seconds: float | None, params: PacketLossParams
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "action": "loss",
        "selector": _selector(target),
        **_mode_fields(target),
        "loss": {"loss": params.loss_percent, "correlation": params.correlation},
    }
    return _with_duration(spec, duration_seconds)


def _spec_io_delay(
    target: K8sTarget, duration_seconds: float | None, params: IODelayParams
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "action": "latency",
        "selector": _selector(target),
        **_mode_fields(target),
        "volumePath": params.volume_path,
        "path": params.path,
        "delay": params.delay,
        "percent": params.percent,
    }
    return _with_duration(spec, duration_seconds)


_BUILDERS = {
    ChaosMeshFaultType.POD_KILL: _spec_pod_kill,
    ChaosMeshFaultType.NETWORK_LATENCY: _spec_network_latency,
    ChaosMeshFaultType.PACKET_LOSS: _spec_packet_loss,
    ChaosMeshFaultType.IO_DELAY: _spec_io_delay,
}


def resource_name(scenario_id: str) -> str:
    """The CR name; the prefix makes KubeHeal-owned objects easy to find."""
    return f"kubeheal-chaos-{scenario_id}"


def build_manifest(scenario: Scenario) -> dict[str, Any]:
    """Return the Chaos Mesh custom resource for a scenario."""
    fault_type = ChaosMeshFaultType(scenario.fault_type)
    target = K8sTarget.model_validate(scenario.target)
    params = parse_params(fault_type, scenario.params)
    spec = _BUILDERS[fault_type](target, scenario.duration_seconds, params)
    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": _KIND[fault_type],
        "metadata": {
            "name": resource_name(scenario.id),
            "namespace": target.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "kubeheal-chaos",
                "kubeheal.io/scenario-id": scenario.id,
            },
        },
        "spec": spec,
    }


def summarize_status(raw_status: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only what says how the experiment is going.

    Chaos Mesh's status also carries `instances` and internal bookkeeping;
    those are noise for an operator and are dropped here.
    """
    if not raw_status:
        return None
    summary: dict[str, Any] = {}
    if "experiment" in raw_status:
        summary["experiment"] = raw_status["experiment"]
    if raw_status.get("conditions"):
        summary["conditions"] = raw_status["conditions"]
    return summary or None


def _custom_objects_api() -> Any:
    """Load cluster credentials lazily and return the CustomObjectsApi."""
    from kubernetes import client, config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()
    return client.CustomObjectsApi()


def _is_not_found(exc: Exception) -> bool:
    return getattr(exc, "status", None) == 404 or "not found" in str(exc).lower()


def _is_conflict(exc: Exception) -> bool:
    return getattr(exc, "status", None) == 409


class ChaosMeshClient:
    """Create, read and delete the custom resources behind each fault type."""

    async def apply(self, manifest: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Apply one manifest; return its name and the applied document."""
        metadata = manifest["metadata"]
        name = metadata["name"]
        namespace = metadata["namespace"]
        plural = _PLURAL_BY_KIND[manifest["kind"]]

        def _create() -> None:
            api = _custom_objects_api()
            try:
                api.create_namespaced_custom_object(
                    group=GROUP,
                    version=VERSION,
                    namespace=namespace,
                    plural=plural,
                    body=manifest,
                )
            except Exception as exc:
                if not _is_conflict(exc):
                    raise
                # A CR from a previous run still exists: replace it rather
                # than failing the scenario on a stale object.
                api.delete_namespaced_custom_object(
                    group=GROUP,
                    version=VERSION,
                    namespace=namespace,
                    plural=plural,
                    name=name,
                )
                api.create_namespaced_custom_object(
                    group=GROUP,
                    version=VERSION,
                    namespace=namespace,
                    plural=plural,
                    body=manifest,
                )

        await asyncio.to_thread(_create)
        return name, manifest

    async def get(
        self, fault_type: ChaosMeshFaultType | str, name: str, namespace: str
    ) -> dict[str, Any] | None:
        """Return the live custom resource, or None when it no longer exists."""
        plural = _PLURAL[ChaosMeshFaultType(fault_type)]

        def _get() -> dict[str, Any] | None:
            api = _custom_objects_api()
            try:
                return api.get_namespaced_custom_object(
                    group=GROUP,
                    version=VERSION,
                    namespace=namespace,
                    plural=plural,
                    name=name,
                )
            except Exception as exc:
                if _is_not_found(exc):
                    return None
                raise

        return await asyncio.to_thread(_get)

    async def delete(
        self, fault_type: ChaosMeshFaultType | str, name: str, namespace: str
    ) -> None:
        """Delete the custom resource; an already-absent one is not an error."""
        plural = _PLURAL[ChaosMeshFaultType(fault_type)]

        def _delete() -> None:
            api = _custom_objects_api()
            try:
                api.delete_namespaced_custom_object(
                    group=GROUP,
                    version=VERSION,
                    namespace=namespace,
                    plural=plural,
                    name=name,
                )
            except Exception as exc:
                if not _is_not_found(exc):
                    raise

        await asyncio.to_thread(_delete)
