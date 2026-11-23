"""Shared fixtures for the agent contract suite.

Every fixture resolves the module it exercises *inside* its own body. Until
`store`, `tools`, `memory`, `nodes` and `routers.runs` exist, importing them
here would break collection for the modules that already pass, so this file
must stay importable against the D2 spike alone.
"""

from __future__ import annotations

import json as jsonlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parent.parent
SRC = SERVICE_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# A scratch directory at import time keeps the store's default `./data/...`
# path from creating files in the repository during collection.
_SCRATCH = Path(tempfile.mkdtemp(prefix="kubeheal-agent-tests-"))
os.environ.setdefault("AGENT_DB_PATH", str(_SCRATCH / "memory.db"))
os.environ.setdefault("AGENT_CHECKPOINT_PATH", str(_SCRATCH / "checkpoints.db"))
os.environ.setdefault("LLM_MODE", "stub")
# `.env` at the repo root supplies the live Azure settings; the suite must
# never select that provider or read a real key, so the names it would use are
# pinned empty. A test that wants them sets them explicitly.
os.environ.setdefault("LLM_PROVIDER", "")
os.environ.setdefault("LLM_BASE_URL", "")
os.environ.setdefault("LLM_API_KEY", "")
os.environ.setdefault("GRAPH_URL", "http://graph.test")
os.environ.setdefault("FAULTLIB_URL", "http://faultlib.test")
os.environ.setdefault("CHAOS_URL", "http://chaos.test")


class FakeResponse:
    """The slice of `httpx.Response` the tools are allowed to touch."""

    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = {} if payload is None and text is None else payload
        self.text = text if text is not None else jsonlib.dumps(self._payload)

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("response has no JSON body")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RecordedCall:
    """One request a tool issued, kept in call order."""

    def __init__(
        self,
        method: str,
        url: Any,
        params: Any = None,
        body: Any = None,
    ) -> None:
        self.method = method
        self.url = url
        self.params = params
        self.body = body

    def __repr__(self) -> str:
        return f"RecordedCall({self.method} {self.url!r} params={self.params!r})"


class FakeHTTPClient:
    """Records calls and replays queued responses, FIFO.

    `enqueue()` is how a test says "the backend answers 502" or "the next
    status is `stopped`"; an unqueued call gets `200 {}`.
    """

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self._queue: list[FakeResponse] = []

    def enqueue(
        self,
        status_code: int = 200,
        payload: Any = None,
        text: str | None = None,
    ) -> FakeHTTPClient:
        self._queue.append(FakeResponse(status_code, payload, text))
        return self

    def _next(self) -> FakeResponse:
        return self._queue.pop(0) if self._queue else FakeResponse(200, {})

    async def get(self, url: Any, params: Any = None, **_kwargs: Any) -> FakeResponse:
        self.calls.append(RecordedCall("GET", url, params))
        return self._next()

    async def post(self, url: Any, json: Any = None, **_kwargs: Any) -> FakeResponse:
        self.calls.append(RecordedCall("POST", url, None, json))
        return self._next()

    def last(self) -> RecordedCall:
        assert self.calls, "the tool issued no request"
        return self.calls[-1]


@pytest.fixture
def service_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test DB paths and backend URLs, set before the app is imported."""
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_CHECKPOINT_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setenv("LLM_MODE", "stub")
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("GRAPH_URL", "http://graph.test")
    monkeypatch.setenv("FAULTLIB_URL", "http://faultlib.test")
    monkeypatch.setenv("CHAOS_URL", "http://chaos.test")
    return tmp_path


@pytest.fixture
def client(service_env: Path) -> Any:
    """`TestClient` around the real app, entered so lifespan runs."""
    from main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def run_store(service_env: Path) -> Any:
    from store import AgentRunStore

    store = AgentRunStore(str(service_env / "memory.db"))
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def memory(service_env: Path) -> Any:
    from memory import IncidentMemory

    return IncidentMemory(str(service_env / "memory.db"))


@pytest.fixture
def stub_provider() -> Any:
    """The deterministic diagnosis shipped with the spike (CONTRACT D7)."""
    from graph import stub_diagnosis

    return stub_diagnosis


@pytest.fixture
def fake_graph_client(monkeypatch: pytest.MonkeyPatch) -> FakeHTTPClient:
    import tools

    fake = FakeHTTPClient()
    monkeypatch.setattr(tools, "graph_client", fake)
    return fake


@pytest.fixture
def fake_faultlib_client(monkeypatch: pytest.MonkeyPatch) -> FakeHTTPClient:
    import tools

    fake = FakeHTTPClient()
    monkeypatch.setattr(tools, "faultlib_client", fake)
    return fake


@pytest.fixture
def fake_chaos_client(monkeypatch: pytest.MonkeyPatch) -> FakeHTTPClient:
    import tools

    fake = FakeHTTPClient()
    monkeypatch.setattr(tools, "chaos_client", fake)
    return fake


class K8sCall:
    """One call a tool issued against the Kubernetes apps/v1 API."""

    def __init__(self, method: str, name: str, namespace: str, body: Any) -> None:
        self.method = method
        self.name = name
        self.namespace = namespace
        self.body = body

    def __repr__(self) -> str:
        return f"K8sCall({self.method} {self.namespace}/{self.name} body={self.body!r})"


class FakeAppsApi:
    """Records `patch_namespaced_*` calls instead of touching a cluster."""

    def __init__(self) -> None:
        self.calls: list[K8sCall] = []
        self.error: Exception | None = None
        self.deployment: Any = None
        self.read_error: Exception | None = None

    def _record(self, method: str, name: str, namespace: str, body: Any) -> Any:
        if self.error is not None:
            raise self.error
        self.calls.append(K8sCall(method, name, namespace, body))
        return body

    def patch_namespaced_deployment(
        self, name: str, namespace: str, body: Any, **_kwargs: Any
    ) -> Any:
        return self._record("PATCH", name, namespace, body)

    def patch_namespaced_deployment_scale(
        self, name: str, namespace: str, body: Any, **_kwargs: Any
    ) -> Any:
        return self._record("PATCH", name, namespace, body)

    def read_namespaced_deployment(
        self, name: str, namespace: str, **_kwargs: Any
    ) -> Any:
        if self.read_error is not None:
            raise self.read_error
        self.calls.append(K8sCall("GET", name, namespace, None))
        return self.deployment

    def last(self) -> K8sCall:
        assert self.calls, "the tool issued no Kubernetes call"
        return self.calls[-1]


class FakeCoreApi:
    """Records `list_namespaced_pod` calls and replays a pod list."""

    def __init__(self) -> None:
        self.calls: list[K8sCall] = []
        self.label_selectors: list[str | None] = []
        self.pods: Any = None
        self.error: Exception | None = None

    def list_namespaced_pod(
        self, namespace: str, label_selector: str | None = None, **_kwargs: Any
    ) -> Any:
        self.calls.append(K8sCall("GET", "pods", namespace, None))
        self.label_selectors.append(label_selector)
        if self.error is not None:
            raise self.error
        return self.pods

    def last(self) -> K8sCall:
        assert self.calls, "the tool issued no pod-list call"
        return self.calls[-1]


@pytest.fixture
def fake_k8s(monkeypatch: pytest.MonkeyPatch) -> FakeAppsApi:
    """Inject recording apps and core clients through the module globals."""
    import tools

    fake = FakeAppsApi()
    core = FakeCoreApi()
    fake.core = core  # type: ignore[attr-defined]
    monkeypatch.setattr(tools, "_apps_api", fake)
    monkeypatch.setattr(tools, "_core_api", core)
    return fake

