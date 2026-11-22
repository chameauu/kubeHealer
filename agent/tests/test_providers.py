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


def test_factory_rejects_an_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_config, "LLM_MODE", "majority-vote")

    with pytest.raises(ValueError) as excinfo:
        providers.build_provider(runtime_config)

    assert "majority-vote" in str(excinfo.value)


# --- wiring and provenance ------------------------------------------------


async def test_provenance_carries_mode_model_and_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class RecordingProvider:
        model = "authored-model"

        def diagnose(self, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(payload)
            return dict(DIAGNOSIS)

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


async def test_default_diagnose_honours_cassette_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = json.loads(DEMO_CASSETTE.read_text(encoding="utf-8"))
    payload = document["demo_payload"]
    monkeypatch.setattr(runtime_config, "LLM_MODE", "cassette")
    monkeypatch.setattr(runtime_config, "CASSETTE_PATH", str(DEMO_CASSETTE))

    state = new_state(str(payload["scenario"]["id"]), payload["scenario"])
    state["blast_radius"] = payload["blast_radius"]
    state["catalog_entry"] = payload["catalog_entry"]

    result = await nodes.diagnose(state)

    diagnosis = result["diagnosis"]
    assert diagnosis["recommended_action"] == "restart_deployment"
    assert diagnosis["diagnosis_provenance"] == {
        "mode": "cassette",
        "model": None,
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
