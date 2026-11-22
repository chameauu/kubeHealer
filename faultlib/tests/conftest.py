"""Shared fixtures for the fault library contract tests.

The service imports its own modules from ``src`` at runtime, so the tests put
both the service root and ``src`` on ``sys.path`` before importing anything.
"""

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = SERVICE_ROOT / "src"
TESTS_DIR = Path(__file__).resolve().parent

for _path in (SERVICE_ROOT, SRC_DIR, TESTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fakes import FakeChaosClient  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A test client that runs the app's lifespan (logging setup included)."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_chaos() -> FakeChaosClient:
    """A fresh in-memory chaos engine for one test."""
    return FakeChaosClient()


@pytest.fixture
def install_chaos(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], Any]:
    """Point the router's module-level ``chaos`` at a double, restored afterwards.

    ``monkeypatch`` guarantees the original singleton is back after the test, so
    test order never leaks a fake into another test.
    """
    import routers.library as library

    def _install(double: Any) -> Any:
        monkeypatch.setattr(library, "chaos", double)
        return double

    return _install
