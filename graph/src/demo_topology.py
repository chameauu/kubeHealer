"""A labelled synthetic topology for offline demos.

Nothing here is measured: every node carries `demo-data=synthetic` and the only
source name is `synthetic_fixture`. The shape is frozen so the console and the
demo script can rely on it.
"""

from __future__ import annotations

from models import Edge, Node, TopologyResponse

DEMO_NAMESPACE = "kubeheal-demo"
DEMO_SOURCE = "synthetic_fixture"
DEMO_LABEL = "synthetic"

_NODE_NAMES: tuple[str, ...] = (
    "auth",
    "cart",
    "database",
    "gateway",
    "inventory",
    "notifications",
    "orders",
    "payments",
    "search",
    "shipping",
    "users",
    "web",
)

# (dependent, dependency, edge_type) - 14 edges over the 12 nodes above.
_EDGES: tuple[tuple[str, str, str], ...] = (
    ("cart", "payments", "flow_observed"),
    ("cart", "web", "env_ref"),
    ("database", "payments", "flow_observed"),
    ("inventory", "orders", "env_ref"),
    ("notifications", "orders", "flow_observed"),
    ("notifications", "users", "env_ref"),
    ("orders", "web", "flow_observed"),
    ("payments", "orders", "env_ref"),
    ("search", "inventory", "flow_observed"),
    ("search", "web", "flow_observed"),
    ("shipping", "inventory", "flow_observed"),
    ("shipping", "payments", "flow_observed"),
    ("users", "auth", "env_ref"),
    ("web", "gateway", "env_ref"),
)


def build_demo_topology() -> TopologyResponse:
    """Return the frozen synthetic topology."""
    nodes = [
        Node(
            id=f"{DEMO_NAMESPACE}/{name}",
            name=name,
            namespace=DEMO_NAMESPACE,
            labels={"app": name, "demo-data": DEMO_LABEL},
        )
        for name in _NODE_NAMES
    ]
    edges = [
        Edge(
            source=f"{DEMO_NAMESPACE}/{source}",
            target=f"{DEMO_NAMESPACE}/{target}",
            edge_type=edge_type,
            flow_count=1 if edge_type == "flow_observed" else 0,
        )
        for source, target, edge_type in sorted(_EDGES)
    ]
    return TopologyResponse(
        nodes=nodes,
        edges=edges,
        topology_sources=[DEMO_SOURCE],
        node_count=len(nodes),
        edge_count=len(edges),
    )
