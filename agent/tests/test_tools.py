"""`tools.py` — the client contracts, including the defect that shipped once.

CONTRACT D1 froze the agent → graph request: `target_namespace`, `fault_type`
and one repeated `selector` per label in sorted key order. The original
implementation sent `namespace`/`label_selector`, graph answered 422, and the
422 was swallowed into an empty blast radius. The exact-query test and the
"parses through the real graph router" test are the regression.

Inputs assumed by this module: `scenario` is the chaos document
(`fault_type` + `target.namespace` + `target.label_selector`) and `blast` is
the selector mapping used for the query.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import pytest
from fastapi import HTTPException

from tools import (
    ToolError,
    get_blast_radius,
    get_catalog_entry,
    restart_deployment,
    scale_deployment,
    stop_scenario,
)

SERVICE_ROOT = Path(__file__).resolve().parents[1]
GRAPH_ROUTER = SERVICE_ROOT.parent / "graph" / "src" / "routers" / "graph.py"

SCENARIO = {
    "id": "scn-1",
    "fault_type": "pod_kill",
    "target": {
        "namespace": "edge",
        "label_selector": {"tier": "web", "app": "api"},
    },
}
LABELS = {"tier": "web", "app": "api"}

EXPECTED_PAIRS = [
    ("target_namespace", "edge"),
    ("fault_type", "pod_kill"),
    ("selector", "app=api"),
    ("selector", "tier=web"),
]
EXPECTED_QUERY = urlencode(EXPECTED_PAIRS)


def _pairs(call: Any) -> list[tuple[str, str]]:
    """The ordered query pairs, whether the tool used `params=` or a URL."""
    if call.params:
        pairs: list[tuple[str, str]] = []
        for key, value in call.params.items():
            values = value if isinstance(value, (list, tuple)) else [value]
            pairs.extend((str(key), str(item)) for item in values)
        return pairs
    return parse_qsl(urlsplit(str(call.url)).query, keep_blank_values=True)


def _graph_selector_parser() -> Any:
    """Load `_parse_selectors` from the real graph router.

    Importing `routers.graph` in this process would collide with the agent's
    own `config` and `models` modules, so the single function is compiled from
    the service's source. The assertion still runs against the shipped parser.
    """
    source = GRAPH_ROUTER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_selectors"
    ]
    assert len(functions) == 1, "graph router no longer exposes its selector parser"
    module = ast.Module(body=functions, type_ignores=[])
    namespace: dict[str, Any] = {"HTTPException": HTTPException}
    exec(compile(module, str(GRAPH_ROUTER), "exec"), namespace)
    return namespace["_parse_selectors"]


async def test_blast_radius_query_matches_d1_exactly(fake_graph_client: Any) -> None:
    await get_blast_radius(SCENARIO, LABELS)

    call = fake_graph_client.last()
    parts = urlsplit(str(call.url))

    assert call.method == "GET"
    assert parts.path == "/blast-radius"
    if parts.scheme:
        assert parts.netloc == "graph.test"
    assert _pairs(call) == EXPECTED_PAIRS
    assert urlencode(_pairs(call)) == EXPECTED_QUERY


async def test_blast_radius_query_parses_through_the_real_graph_router(
    fake_graph_client: Any,
) -> None:
    await get_blast_radius(SCENARIO, LABELS)

    pairs = _pairs(fake_graph_client.last())
    selectors = [value for key, value in pairs if key == "selector"]
    parsed = _graph_selector_parser()(selectors)

    assert parsed == {"app": "api", "tier": "web"}


async def test_selector_values_are_url_encoded(fake_graph_client: Any) -> None:
    scenario = {
        **SCENARIO,
        "target": {"namespace": "edge", "label_selector": {"app": "checkout api"}},
    }

    await get_blast_radius(scenario, {"app": "checkout api"})

    # quote_via=quote, not quote_plus: the wire encoding httpx uses is %20, so a
    # plus-encoded query would assert a different request than the one sent.
    query = urlencode(_pairs(fake_graph_client.last()), quote_via=quote)
    assert "selector=app%3Dcheckout%20api" in query


async def test_graph_error_is_logged_and_returned_as_empty(fake_graph_client: Any) -> None:
    fake_graph_client.enqueue(422, {"detail": [{"msg": "selector missing"}]})

    result = await get_blast_radius(SCENARIO, LABELS)

    assert result == {}


async def test_catalog_entry_reads_the_faultlib_path(fake_faultlib_client: Any) -> None:
    entry = {"domain": "chaos_mesh", "fault_type": "pod_kill", "authored": True}
    fake_faultlib_client.enqueue(200, entry)

    result = await get_catalog_entry("chaos_mesh", "pod_kill")

    call = fake_faultlib_client.last()
    assert call.method == "GET"
    assert urlsplit(str(call.url)).path == "/catalog/chaos_mesh/pod_kill"
    assert result == entry


async def test_catalog_404_returns_empty(fake_faultlib_client: Any) -> None:
    fake_faultlib_client.enqueue(404, {"detail": "no entry"})

    assert await get_catalog_entry("chaos_mesh", "pod_kill") == {}


async def test_stop_scenario_posts_the_stop_route(fake_chaos_client: Any) -> None:
    fake_chaos_client.enqueue(200, {"id": "scn-1", "status": "stopping"})

    result = await stop_scenario("scn-1")

    call = fake_chaos_client.last()
    assert call.method == "POST"
    assert urlsplit(str(call.url)).path == "/scenarios/scn-1/stop"
    assert result["status"] == "stopping"


async def test_chaos_502_surfaces_as_tool_error(fake_chaos_client: Any) -> None:
    fake_chaos_client.enqueue(502, {"detail": "chaos unreachable: connection refused"})

    with pytest.raises(ToolError):
        await stop_scenario("scn-1")


async def test_restart_deployment_sends_both_annotations(fake_kubectl: Any) -> None:
    await restart_deployment("edge", "api")

    assert len(fake_kubectl) == 1
    argv = fake_kubectl[0]
    blob = " ".join(argv)
    assert "edge" in argv
    assert "api" in argv
    assert "kubectl.kubernetes.io/restartedAt" in blob
    assert "kubeheal-agent/restarted-by" in blob


async def test_scale_deployment_sends_replicas(fake_kubectl: Any) -> None:
    await scale_deployment("edge", "api", 3)

    assert len(fake_kubectl) == 1
    argv = fake_kubectl[0]
    blob = " ".join(argv)
    assert "edge" in argv
    assert "api" in argv
    assert ("--replicas" in argv and "3" in argv) or "replicas=3" in blob


def test_the_expected_query_is_the_documented_one() -> None:
    """Guard the fixture itself: D1's exact string, one selector per label."""
    assert EXPECTED_QUERY == (
        "target_namespace=edge&fault_type=pod_kill"
        "&selector=app%3Dapi&selector=tier%3Dweb"
    )
    assert json.dumps(EXPECTED_PAIRS) == (
        '[["target_namespace", "edge"], ["fault_type", "pod_kill"], '
        '["selector", "app=api"], ["selector", "tier=web"]]'
    )
