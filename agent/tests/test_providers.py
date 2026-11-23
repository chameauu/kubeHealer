"""`providers.py` — the three D7 providers behind one seam, exercised offline.

No test opens a socket, needs a key or calls OpenAI. `OpenAIProvider` is driven
through a fake `openai` module injected into `sys.modules`, so fence handling
and bad output are covered without the real SDK.

Fixtures are authored, not captured: both cassettes carry
`"captured_from": "authored_fixture"` and neither claims a live recording.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from config import config as runtime_config
import nodes
import providers
from state import new_state

TESTS_DIR = Path(__file__).resolve().parent
CASSETTE = TESTS_DIR / "fixtures" / "cassette.json"
DEMO_CASSETTE = TESTS_DIR.parent / "fixtures" / "diagnosis-cassette.json"

SCENARIO = {
    "id": "sc-prov",
    "fault_type": "pod_kill",
    "target": {"namespace": "edge", "label_selector": {"app": "api"}},
}

DIAGNOSIS = {
    "causal_chain": ["pod terminated", "no ready replicas", "downstream 503s"],
    "recommended_action": "restart_deployment",
    "action_target": "edge/api",
    "risk": "high",
    "rationale": "the deployment has no ready replicas; a restart restores capacity",
}

CASSETTE_PAYLOAD = {
    "scenario": {
        "id": "sc-cassette",
        "fault_type": "pod_kill",
        "target": {"namespace": "edge", "label_selector": {"app": "api"}},
    },
    "blast_radius": {"affected_nodes": [{"node_id": "edge/api", "distance_hops": 0}]},
    "catalog_entry": {"domain": "chaos_mesh", "fault_type": "pod_kill"},
    "memory_context": "2 prior incidents, restart_deployment succeeded twice",
}


def _state(**overrides: Any) -> Any:
    state = new_state(str(SCENARIO["id"]), dict(SCENARIO))
    state.update(overrides)
    return state


def _canonical(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch, content: str) -> Any:
    """A module enough like `openai` that construction and one call succeed."""

    module = ModuleType("openai")

    def create(**_kwargs: Any) -> Any:
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeOpenAI:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    module.OpenAI = FakeOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return module


# --- stub -----------------------------------------------------------------


def test_stub_provider_returns_the_demo_diagnosis() -> None:
    diagnosis = providers.StubProvider().diagnose({"scenario": dict(SCENARIO)})

    assert diagnosis["recommended_action"] == "restart_deployment"
    assert diagnosis["action_target"] == "edge/api"
    assert diagnosis["risk"] == "high"
    assert diagnosis["causal_chain"]


def test_stub_provider_is_deterministic() -> None:
    provider = providers.StubProvider()

    first = provider.diagnose(CASSETTE_PAYLOAD)
    second = provider.diagnose(CASSETTE_PAYLOAD)

    assert first == second
    assert first is not second
    assert provider.model is None


def test_stub_provider_matches_the_graph_fixture() -> None:
    from graph import stub_diagnosis

    assert providers.StubProvider().diagnose(CASSETTE_PAYLOAD) == stub_diagnosis(
        **CASSETTE_PAYLOAD
    )


# --- canonical hash -------------------------------------------------------


def test_prompt_sha256_is_canonical_and_order_sensitive() -> None:
    first = {"b": 2, "a": {"d": 4, "c": 3}}
    second = {"a": {"c": 3, "d": 4}, "b": 2}

    assert providers.prompt_sha256(first) == providers.prompt_sha256(second)
    assert len(providers.prompt_sha256(first)) == 64
    assert providers.prompt_sha256({"a": [1, 2]}) != providers.prompt_sha256(
        {"a": [2, 1]}
    )


# --- cassette -------------------------------------------------------------


def test_cassette_replays_a_hit_byte_identically() -> None:
    document = json.loads(CASSETTE.read_text(encoding="utf-8"))
    stored = document[providers.prompt_sha256(CASSETTE_PAYLOAD)]

    provider = providers.CassetteProvider(CASSETTE)
    replayed = provider.diagnose(CASSETTE_PAYLOAD)

    assert _canonical(replayed) == _canonical(stored)
    assert provider.model is None
    assert document["captured_from"] == "authored_fixture"


def test_cassette_replay_is_isolated_from_caller_mutation() -> None:
    provider = providers.CassetteProvider(CASSETTE)

    first = provider.diagnose(CASSETTE_PAYLOAD)
    first["risk"] = "low"
    second = provider.diagnose(CASSETTE_PAYLOAD)

    assert second["risk"] == "high"


def test_cassette_exposes_the_model_it_was_recorded_with(tmp_path: Path) -> None:
    payload = {"scenario": {"id": "sc-model"}}
    document = {
        "captured_from": "authored_fixture",
        "model": "gpt-4o-mini",
        providers.prompt_sha256(payload): dict(DIAGNOSIS),
    }
    path = tmp_path / "model-cassette.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    provider = providers.CassetteProvider(path)

    assert provider.model == "gpt-4o-mini"
    assert provider.diagnose(payload) == DIAGNOSIS


def test_cassette_miss_names_the_hash_prefix() -> None:
    unknown = {"scenario": {"id": "sc-miss"}}
    digest = providers.prompt_sha256(unknown)
    provider = providers.CassetteProvider(CASSETTE)

    with pytest.raises(providers.DiagnosisError) as excinfo:
        provider.diagnose(unknown)

    assert digest[:12] in str(excinfo.value)


def test_cassette_rejects_a_single_changed_field() -> None:
    changed = {**CASSETTE_PAYLOAD, "memory_context": "no prior incidents"}
    assert providers.prompt_sha256(changed) != providers.prompt_sha256(CASSETTE_PAYLOAD)

    with pytest.raises(providers.DiagnosisError):
        providers.CassetteProvider(CASSETTE).diagnose(changed)


def test_cassette_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(providers.DiagnosisError):
        providers.CassetteProvider(path)


def test_cassette_rejects_a_non_object_document(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(providers.DiagnosisError):
        providers.CassetteProvider(path)


def test_cassette_rejects_an_entry_that_is_not_an_object(tmp_path: Path) -> None:
    path = tmp_path / "bad-entry.json"
    path.write_text(json.dumps({"a" * 64: "not a diagnosis"}), encoding="utf-8")

    with pytest.raises(providers.DiagnosisError):
        providers.CassetteProvider(path)


def test_cassette_rejects_a_file_with_no_entries(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"captured_from": "authored_fixture"}), encoding="utf-8")

    with pytest.raises(providers.DiagnosisError):
        providers.CassetteProvider(path)


# --- openai ---------------------------------------------------------------


def test_openai_construction_without_a_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(providers.DiagnosisError, match="OPENAI_API_KEY is not set"):
        providers.OpenAIProvider()


def test_openai_strips_markdown_fences(monkeypatch: pytest.MonkeyPatch) -> None:
    fenced = "```json\n" + json.dumps(DIAGNOSIS) + "\n```"
    _install_fake_openai(monkeypatch, fenced)

    provider = providers.OpenAIProvider()

    assert provider.model == runtime_config.LLM_MODEL
    assert provider.diagnose(CASSETTE_PAYLOAD) == DIAGNOSIS


def test_openai_rejects_output_that_is_not_a_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch, "[1, 2, 3]")

    with pytest.raises(providers.DiagnosisError, match="not a JSON object"):
        providers.OpenAIProvider().diagnose(CASSETTE_PAYLOAD)


def test_openai_rejects_unparseable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch, "the model declined")

    with pytest.raises(providers.DiagnosisError, match="unparseable diagnosis"):
        providers.OpenAIProvider().diagnose(CASSETTE_PAYLOAD)


def test_providers_module_imports_with_openai_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "openai", raising=False)

    module = importlib.reload(providers)

    assert hasattr(module, "OpenAIProvider")
    assert "openai" not in sys.modules


# --- azure ai foundry (OpenAI-compatible v1) ------------------------------

FOUNDRY_VARS = (
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
)
FOUNDRY_BASE_URL = "https://kamelcv.services.ai.azure.com/openai/v1"
FOUNDRY_MODEL = "gpt-4.1-mini"


@pytest.fixture
def foundry_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Clean settings, then the LLM_* names the service really uses."""
    for name in FOUNDRY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(runtime_config, name, "", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "azure-v1")
    monkeypatch.setenv("LLM_BASE_URL", FOUNDRY_BASE_URL)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", FOUNDRY_MODEL)
    return {
        "base_url": FOUNDRY_BASE_URL,
        "api_key": "test-key",
        "model": FOUNDRY_MODEL,
    }


class _FakeFoundryClient:
    """The slice of the OpenAI client the provider touches, recording calls."""

    def __init__(self, content: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.content = content
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _install_fake_foundry(
    monkeypatch: pytest.MonkeyPatch, content: Any = json.dumps(DIAGNOSIS)
) -> tuple[_FakeFoundryClient, dict[str, Any]]:
    """Install the client seam; return the client and the factory's kwargs."""
    client = _FakeFoundryClient(content)
    factory_kwargs: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeFoundryClient:
        factory_kwargs.update(kwargs)
        return client

    monkeypatch.setattr(providers, "_azure_client_factory", factory, raising=False)
    return client, factory_kwargs


def test_foundry_construction_requires_a_base_url(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    with pytest.raises(providers.DiagnosisError, match="LLM_BASE_URL is not set"):
        providers.AzureFoundryProvider()


def test_foundry_construction_requires_a_key(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(providers.DiagnosisError, match="LLM_API_KEY is not set"):
        providers.AzureFoundryProvider()


def test_foundry_construction_requires_a_model(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(providers.DiagnosisError, match="LLM_MODEL is not set"):
        providers.AzureFoundryProvider()


def test_foundry_missing_settings_are_reported_without_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in FOUNDRY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(runtime_config, name, "", raising=False)
    monkeypatch.delitem(sys.modules, "openai", raising=False)

    with pytest.raises(providers.DiagnosisError, match="LLM_BASE_URL is not set"):
        providers.AzureFoundryProvider()

    assert "openai" not in sys.modules


def test_foundry_passes_base_url_and_key_to_the_factory(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, factory_kwargs = _install_fake_foundry(monkeypatch)

    providers.AzureFoundryProvider()

    assert factory_kwargs == {
        "base_url": FOUNDRY_BASE_URL,
        "api_key": "test-key",
    }


def test_foundry_sends_the_llm_model_in_the_request(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _install_fake_foundry(monkeypatch)

    diagnosis = providers.AzureFoundryProvider().diagnose(CASSETTE_PAYLOAD)

    assert diagnosis == DIAGNOSIS
    call = client.calls[0]
    assert call["model"] == FOUNDRY_MODEL
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1] == {
        "role": "user",
        "content": _canonical(CASSETTE_PAYLOAD),
    }
    assert call["temperature"] == 0


def test_foundry_uses_the_azure_names_as_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in FOUNDRY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(runtime_config, name, "", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://alias.example/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "alias-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "alias-model")
    _, factory_kwargs = _install_fake_foundry(monkeypatch)

    provider = providers.AzureFoundryProvider()

    assert factory_kwargs["base_url"] == "https://alias.example/openai/v1"
    assert factory_kwargs["api_key"] == "alias-key"
    assert provider.model == "alias-model"


def test_foundry_prefers_llm_names_over_the_aliases(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://alias.example/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "alias-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "alias-model")
    _, factory_kwargs = _install_fake_foundry(monkeypatch)

    provider = providers.AzureFoundryProvider()

    assert factory_kwargs["base_url"] == FOUNDRY_BASE_URL
    assert factory_kwargs["api_key"] == "test-key"
    assert provider.model == FOUNDRY_MODEL


@pytest.mark.parametrize(
    "content",
    [
        json.dumps(DIAGNOSIS),
        "```json\n" + json.dumps(DIAGNOSIS) + "\n```",
    ],
)
def test_foundry_parses_fenced_and_unfenced_json(
    foundry_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    _install_fake_foundry(monkeypatch, content)

    assert providers.AzureFoundryProvider().diagnose(CASSETTE_PAYLOAD) == DIAGNOSIS


def test_foundry_rejects_output_that_is_not_a_json_object(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_foundry(monkeypatch, "[1, 2, 3]")

    with pytest.raises(providers.DiagnosisError, match="not a JSON object"):
        providers.AzureFoundryProvider().diagnose(CASSETTE_PAYLOAD)


def test_foundry_rejects_an_empty_choices_list(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class EmptyClient:
        chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(choices=[])
            )
        )

    monkeypatch.setattr(
        providers, "_azure_client_factory", lambda **_kwargs: EmptyClient(), raising=False
    )

    with pytest.raises(providers.DiagnosisError):
        providers.AzureFoundryProvider().diagnose(CASSETTE_PAYLOAD)


def test_foundry_names_the_exception_when_the_sdk_error_is_silent(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class SilentSdkError(Exception):
        pass

    class FailingClient:
        chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: (_ for _ in ()).throw(SilentSdkError())
            )
        )

    monkeypatch.setattr(
        providers,
        "_azure_client_factory",
        lambda **_kwargs: FailingClient(),
        raising=False,
    )

    with pytest.raises(providers.DiagnosisError, match="SilentSdkError"):
        providers.AzureFoundryProvider().diagnose(CASSETTE_PAYLOAD)


# --- factory --------------------------------------------------------------


def test_factory_selects_each_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch, json.dumps(DIAGNOSIS))
    monkeypatch.setattr(runtime_config, "LLM_MODE", "stub")
    assert isinstance(providers.build_provider(runtime_config), providers.StubProvider)

    monkeypatch.setattr(runtime_config, "LLM_MODE", "cassette")
    monkeypatch.setattr(runtime_config, "CASSETTE_PATH", str(CASSETTE))
    assert isinstance(
        providers.build_provider(runtime_config), providers.CassetteProvider
    )

    monkeypatch.setattr(runtime_config, "LLM_MODE", "openai")
    monkeypatch.setattr(runtime_config, "LLM_MODEL", "gpt-test")
    live = providers.build_provider(runtime_config)
    assert isinstance(live, providers.OpenAIProvider)
    assert live.model == "gpt-test"


@pytest.mark.parametrize("mode", ["azure", "azure-v1", "foundry"])
def test_factory_selects_each_foundry_mode(
    foundry_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    _install_fake_foundry(monkeypatch)
    monkeypatch.setattr(runtime_config, "LLM_PROVIDER", mode)
    monkeypatch.setattr(runtime_config, "LLM_BASE_URL", FOUNDRY_BASE_URL)
    monkeypatch.setattr(runtime_config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(runtime_config, "LLM_MODEL", FOUNDRY_MODEL)

    provider = providers.build_provider(runtime_config)

    assert isinstance(provider, providers.AzureFoundryProvider)
    assert provider.model == FOUNDRY_MODEL


def test_factory_falls_back_to_llm_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_config, "LLM_PROVIDER", "")
    monkeypatch.setattr(runtime_config, "LLM_MODE", "azure")
    monkeypatch.setattr(runtime_config, "LLM_BASE_URL", FOUNDRY_BASE_URL)
    monkeypatch.setattr(runtime_config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(runtime_config, "LLM_MODEL", FOUNDRY_MODEL)
    _install_fake_foundry(monkeypatch)

    assert isinstance(
        providers.build_provider(runtime_config), providers.AzureFoundryProvider
    )


def test_factory_rejects_an_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_config, "LLM_MODE", "majority-vote")

    with pytest.raises(ValueError) as excinfo:
        providers.build_provider(runtime_config)

    assert "majority-vote" in str(excinfo.value)


# --- wiring and provenance ------------------------------------------------


class _EmptyMemory:
    """An empty incident store: the payloads below predate incident memory."""

    async def recall(self, fault_type: str, limit: int = 20) -> dict[str, Any]:
        return {
            "incidents": [],
            "success_rate": 0.0,
            "confidence": 0,
            "mean_mttr_seconds": None,
        }


async def test_provenance_carries_mode_model_and_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class RecordingProvider:
        model = "authored-model"

        def diagnose(self, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(payload)
            return dict(DIAGNOSIS)

    # `diagnose` now reads incident memory, which the session-wide DB shares
    # between test files; these assertions are about the payload contract.
    monkeypatch.setattr(nodes, "memory", _EmptyMemory())
    blast = {"affected_nodes": [{"node_id": "edge/api", "distance_hops": 0}]}
    catalog = {"domain": "chaos_mesh", "fault_type": "pod_kill"}
    state = _state(blast_radius=blast, catalog_entry=catalog)

    result = await nodes.diagnose(state, provider=RecordingProvider())

    expected_payload = {
        "scenario": SCENARIO,
        "blast_radius": blast,
        "catalog_entry": catalog,
        "memory_context": None,
    }
    provenance = result["diagnosis"]["diagnosis_provenance"]
    assert calls == [expected_payload]
    assert provenance["mode"] == runtime_config.LLM_MODE
    assert provenance["model"] == "authored-model"
    assert provenance["prompt_sha256"] == providers.prompt_sha256(expected_payload)


async def test_foundry_provenance_records_mode_verbatim_and_model(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_foundry(monkeypatch)
    monkeypatch.setattr(runtime_config, "LLM_PROVIDER", "azure-v1")
    monkeypatch.setattr(runtime_config, "LLM_BASE_URL", FOUNDRY_BASE_URL)
    monkeypatch.setattr(runtime_config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(runtime_config, "LLM_MODEL", FOUNDRY_MODEL)
    monkeypatch.setattr(nodes, "memory", _EmptyMemory())
    blast = {"affected_nodes": [{"node_id": "edge/api", "distance_hops": 0}]}
    catalog = {"domain": "chaos_mesh", "fault_type": "pod_kill"}
    state = _state(blast_radius=blast, catalog_entry=catalog)
    expected_payload = {
        "scenario": SCENARIO,
        "blast_radius": blast,
        "catalog_entry": catalog,
        "memory_context": None,
    }

    result = await nodes.diagnose(state, provider=providers.AzureFoundryProvider())

    assert result["diagnosis"]["diagnosis_provenance"] == {
        "mode": "azure-v1",
        "model": FOUNDRY_MODEL,
        "prompt_sha256": providers.prompt_sha256(expected_payload),
    }


async def test_default_diagnose_honours_cassette_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = json.loads(DEMO_CASSETTE.read_text(encoding="utf-8"))
    payload = document["demo_payload"]
    monkeypatch.setattr(runtime_config, "LLM_MODE", "cassette")
    monkeypatch.setattr(runtime_config, "CASSETTE_PATH", str(DEMO_CASSETTE))
    # The cassette was authored with `memory_context: null`, and a cassette
    # replays only on an identical payload: pin the empty store it assumed.
    monkeypatch.setattr(nodes, "memory", _EmptyMemory())

    state = new_state(str(payload["scenario"]["id"]), payload["scenario"])
    state["blast_radius"] = payload["blast_radius"]
    state["catalog_entry"] = payload["catalog_entry"]

    result = await nodes.diagnose(state)

    diagnosis = result["diagnosis"]
    assert diagnosis["recommended_action"] == "restart_deployment"
    # The shipped demo cassette is recorded from a live Foundry call, so it
    # declares the model it came from instead of leaving it null.
    assert diagnosis["diagnosis_provenance"] == {
        "mode": "cassette",
        "model": FOUNDRY_MODEL,
        "prompt_sha256": providers.prompt_sha256(payload),
    }


def test_app_startup_rejects_an_unknown_llm_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setattr(runtime_config, "LLM_MODE", "majority-vote")

    with pytest.raises(ValueError):
        with TestClient(main.app):
            pass


def test_foundry_sends_the_system_prompt(
    foundry_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Azure path used to send only the user message, so the model was never
    # told to answer with JSON. It replied in prose and every diagnosis fell
    # back to stop_scenario.
    client, _ = _install_fake_foundry(monkeypatch)

    providers.AzureFoundryProvider().diagnose({"scenario": {"id": "sc-1"}})

    messages = client.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "JSON" in messages[0]["content"]
    assert messages[1]["role"] == "user"


def test_prose_wrapping_an_object_is_parsed() -> None:
    text = 'Here is the diagnosis:\n{"recommended_action": "stop_scenario"}\nDone.'

    assert providers.parse_diagnosis_text(text) == {"recommended_action": "stop_scenario"}


def test_prose_without_an_object_names_a_snippet() -> None:
    with pytest.raises(providers.DiagnosisError, match="I cannot help with that"):
        providers.parse_diagnosis_text("I cannot help with that request.")
