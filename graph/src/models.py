"""Typed contracts for the topology graph and the blast radius.

Every field name here is part of the HTTP contract: the dashboard generates its
types from the exported schema, and the agent asserts the query keys it sends.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

EdgeType = Literal["env_ref", "flow_observed"]
Severity = Literal["high", "medium", "low"]


def utcnow_iso() -> str:
    """Return an ISO-8601 UTC timestamp ending in `Z`."""
    now = datetime.now(timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Node(BaseModel):
    """One Service in the graph. `id` is `"<namespace>/<name>"`."""

    id: str
    name: str
    namespace: str
    kind: str = "Service"
    labels: dict[str, str] = Field(default_factory=dict)
    cluster_ip: str | None = None


class Edge(BaseModel):
    """A directed link from the dependent to its dependency."""

    source: str
    target: str
    edge_type: EdgeType
    flow_count: int = 0
    env_var: str | None = None


class TopologyResponse(BaseModel):
    """The whole graph, plus the list of sources that answered."""

    nodes: list[Node]
    edges: list[Edge]
    topology_sources: list[str]
    node_count: int
    edge_count: int
    observed_at: str = Field(default_factory=utcnow_iso)


class AffectedNode(BaseModel):
    """A node reached by walking the dependent graph backwards."""

    node_id: str
    name: str
    namespace: str
    distance_hops: int
    severity: Severity
    via_edge_types: list[str] = Field(default_factory=list)


class BlastRadiusResponse(BaseModel):
    """The blast radius of one fault class against one selector."""

    target_namespace: str
    target_selector: dict[str, str]
    fault_type: str
    matched_nodes: list[str]
    affected_nodes: list[AffectedNode]
    topology_sources: list[str]
    computed_at: str = Field(default_factory=utcnow_iso)
