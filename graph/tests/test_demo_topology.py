"""The demo fixture is a labelled synthetic topology, never a measurement.

Every node carries `demo-data=synthetic`, the source list names the fixture, and
the shape is fixed so the console and the demo script can rely on it.
"""

from __future__ import annotations

from demo_topology import build_demo_topology


def test_demo_fixture_has_the_frozen_shape() -> None:
    topology = build_demo_topology()

    assert len(topology.nodes) == 12
    assert len(topology.edges) == 14
    assert topology.node_count == 12
    assert topology.edge_count == 14
    assert topology.topology_sources == ["synthetic_fixture"]
    assert all(node.labels["demo-data"] == "synthetic" for node in topology.nodes)
