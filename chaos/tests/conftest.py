"""Fixtures for the chaos contract suite.

The service is judged on persistence, HTTP envelopes and state transitions, so
every test starts from an empty in-memory store and fresh backends. The app's
singleton engine is replaced with a fake-backed instance per test; nothing the
previous test wrote can leak forward.

`CHAOS_DB_PATH` is pinned before any application module is imported, so the
service's own startup cannot open a file database while a test runs.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

os.environ["CHAOS_DB_PATH"] = ":memory:"

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_SRC = _SERVICE_ROOT / "src"
for _path in (str(_SERVICE_ROOT), str(_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tests.fakes import FakeChaosMeshClient, FakeSimulatorClient  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_app_engine() -> Iterator[None]:
    """Drop the engine the previous test attached to the app."""
    yield
    from main import app

    app.state.engine = None


@pytest.fixture
def fake_chaos_mesh() -> FakeChaosMeshClient:
    return FakeChaosMeshClient()


@pytest.fixture
def fake_simulator() -> FakeSimulatorClient:
    return FakeSimulatorClient()


@pytest.fixture
def events() -> list[dict[str, Any]]:
    """Every event the engine publishes, in order."""
    return []


@pytest.fixture
async def scenario_store() -> AsyncIterator[Any]:
    from store import ScenarioStore

    store = ScenarioStore(db_path=":memory:")
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def make_engine(
    scenario_store: Any,
    fake_chaos_mesh: FakeChaosMeshClient,
    fake_simulator: FakeSimulatorClient,
    events: list[dict[str, Any]],
) -> Callable[..., Any]:
    """Build an engine over the per-test store, with overridable backends."""
    from engine import ScenarioEngine

    def _make(*, chaos_mesh: Any = None, simulator: Any = None) -> Any:
        return ScenarioEngine(
            store=scenario_store,
            chaos_mesh=fake_chaos_mesh if chaos_mesh is None else chaos_mesh,
            simulator=fake_simulator if simulator is None else simulator,
            sweep_interval_seconds=0.05,
            event_sink=events.append,
        )

    return _make


@pytest.fixture
async def engine(make_engine: Callable[..., Any]) -> AsyncIterator[Any]:
    instance = make_engine()
    try:
        yield instance
    finally:
        await instance.stop_sweeper()


@pytest.fixture
def make_client() -> Iterator[Callable[[Any], TestClient]]:
    """Attach any engine to the app and return a client bound to it."""
    from main import app

    def _make(engine: Any) -> TestClient:
        app.state.engine = engine
        return TestClient(app)

    yield _make
    app.state.engine = None


@pytest.fixture
def client(make_client: Callable[[Any], TestClient], engine: Any) -> TestClient:
    return make_client(engine)
