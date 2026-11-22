"""Fixtures for the graph contract suite.

Every test that exercises the routes swaps both topology sources for in-memory
doubles and restores them afterwards through `monkeypatch`, so no test can leak
a fake into the next one. `routers.graph` is imported lazily inside the
fixtures: the pre-existing health and openapi tests must keep collecting even
before the router exists.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_SRC = _SERVICE_ROOT / "src"
for _path in (str(_SERVICE_ROOT), str(_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def install_backends(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Replace `routers.graph.k8s` / `.hubble` for one test."""

    import routers.graph as graph_router

    def _install(*, k8s: Any = None, hubble: Any = None) -> None:
        if k8s is not None:
            monkeypatch.setattr(graph_router, "k8s", k8s)
        if hubble is not None:
            monkeypatch.setattr(graph_router, "hubble", hubble)

    return _install


@pytest.fixture
def demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve the labelled synthetic fixture instead of the cluster."""

    import routers.graph as graph_router

    monkeypatch.setattr(graph_router.config, "LOCAL_DEMO", True)
