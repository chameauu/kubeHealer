"""In-memory doubles for the two topology sources.

The builder only ever sees the client seams, so these doubles are the whole
backend contract for `graph`: what Kubernetes reports, what Hubble reports, and
how many times each source was asked. Every list is deep-copied on the way out
so a test that mutates a response cannot corrupt the next assertion.

The data shapes are the ones the builder is specified against: services, pods,
ReplicaSets and flows as plain dictionaries.
"""

from __future__ import annotations

import copy
from typing import Any


def service(
    name: str,
    namespace: str,
    *,
    labels: dict[str, str] | None = None,
    selector: dict[str, str] | None = None,
    cluster_ip: str = "10.0.0.10",
) -> dict[str, Any]:
    """A Kubernetes Service whose selector matches pods labelled `app=<name>`."""
    return {
        "name": name,
        "namespace": namespace,
        "labels": dict(labels) if labels is not None else {"app": name},
        "selector": dict(selector) if selector is not None else {"app": name},
        "cluster_ip": cluster_ip,
    }


def replicaset(name: str, namespace: str, *, deployment_name: str) -> dict[str, Any]:
    """A ReplicaSet: the only place the Pod -> Deployment owner link is recorded."""
    return {
        "name": name,
        "namespace": namespace,
        "deployment_name": deployment_name,
    }


def pod(
    name: str,
    namespace: str,
    *,
    owner_name: str,
    owner_kind: str = "ReplicaSet",
    env: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    node_name: str = "node-a",
) -> dict[str, Any]:
    return {
        "name": name,
        "namespace": namespace,
        "labels": dict(labels) if labels is not None else {},
        "owner_name": owner_name,
        "owner_kind": owner_kind,
        "node_name": node_name,
        "env": dict(env) if env is not None else {},
    }


def workload(
    namespace: str,
    deployment: str,
    *,
    env: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    replicaset_name: str | None = None,
    pod_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One Deployment's ReplicaSet and one Pod, owned through that ReplicaSet."""
    rs_name = replicaset_name or f"{deployment}-6f7c9d4b8"
    replicaset_row = replicaset(rs_name, namespace, deployment_name=deployment)
    pod_row = pod(
        pod_name or f"{rs_name}-abcde",
        namespace,
        owner_name=rs_name,
        env=env,
        labels={"app": deployment} if labels is None else labels,
    )
    return replicaset_row, pod_row


def flow(
    source_ns: str,
    source_workload: str,
    dest_ns: str,
    dest_workload: str,
) -> dict[str, Any]:
    """One observed request from a caller workload to a callee workload."""
    return {
        "source_ns": source_ns,
        "source_workload": source_workload,
        "dest_ns": dest_ns,
        "dest_workload": dest_workload,
    }


class FakeK8s:
    """A Kubernetes API that answers from lists and counts every call."""

    def __init__(
        self,
        *,
        services: list[dict[str, Any]] | None = None,
        pods: list[dict[str, Any]] | None = None,
        replicasets: list[dict[str, Any]] | None = None,
        fail: bool = False,
    ) -> None:
        self.services = list(services or [])
        self.pods = list(pods or [])
        self.replicasets = list(replicasets or [])
        self.fail = fail
        self.service_calls = 0
        self.pod_calls = 0
        self.replicaset_calls = 0

    def _refuse(self) -> None:
        if self.fail:
            raise RuntimeError("kubernetes API unreachable")

    async def list_services(self) -> list[dict[str, Any]]:
        self.service_calls += 1
        self._refuse()
        return copy.deepcopy(self.services)

    async def list_pods(self) -> list[dict[str, Any]]:
        self.pod_calls += 1
        self._refuse()
        return copy.deepcopy(self.pods)

    async def list_replicasets(self) -> list[dict[str, Any]]:
        self.replicaset_calls += 1
        self._refuse()
        return copy.deepcopy(self.replicasets)


class FakeHubble:
    """A Hubble flow source that answers from a list and records its arguments."""

    def __init__(
        self,
        *,
        flows: list[dict[str, Any]] | None = None,
        fail: bool = False,
    ) -> None:
        self.flows = list(flows or [])
        self.fail = fail
        self.flow_calls = 0
        self.max_flows_seen: list[int] = []

    async def get_flows(self, max_flows: int = 1000) -> list[dict[str, Any]]:
        self.flow_calls += 1
        self.max_flows_seen.append(max_flows)
        if self.fail:
            raise RuntimeError("hubble relay unreachable")
        return copy.deepcopy(self.flows)
