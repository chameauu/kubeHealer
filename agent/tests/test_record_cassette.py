"""`record_cassette.py` — record one Azure Foundry diagnosis without the SDK.

Every test injects the client factory seam and sets fake `LLM_*` credentials,
so the suite never imports `openai` and never opens a socket. The
missing-credential path is asserted to leave no file behind: a half-written
cassette would replay as a real diagnosis.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config import config as runtime_config
import providers
import record_cassette

FOUNDRY_VARS = (
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
)
FOUNDRY_MODEL = "gpt-4.1-mini"

PAYLOAD = {
    "scenario": {
        "id": "sc-record",
        "fault_type": "pod_kill",
        "target": {"namespace": "edge", "label_selector": {"app": "api"}},
    },
    "blast_radius": {"affected_nodes": [{"node_id": "edge/api", "distance_hops": 0}]},
    "catalog_entry": {"domain": "chaos_mesh", "fault_type": "pod_kill"},
    "memory_context": None,
}

DIAGNOSIS = {
    "causal_chain": ["pod terminated", "no ready replicas"],
    "recommended_action": "restart_deployment",
    "action_target": "edge/api",
    "risk": "high",
    "rationale": "no ready replicas remain",
}

DIGEST = providers.prompt_sha256(PAYLOAD)


@pytest.fixture
def foundry_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake credentials in the environment; no real endpoint is contacted."""
    for name in FOUNDRY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(runtime_config, name, "", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "azure-v1")
    monkeypatch.setenv("LLM_BASE_URL", "https://kamelcv.services.ai.azure.com/openai/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", FOUNDRY_MODEL)


class _FakeFoundryClient:
    """Returns one fixed diagnosis and records the request it was given."""

    def __init__(self, content: str = json.dumps(DIAGNOSIS)) -> None:
        self.calls: list[dict[str, Any]] = []
        self.content = content
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def fake_foundry(monkeypatch: pytest.MonkeyPatch) -> _FakeFoundryClient:
    client = _FakeFoundryClient()
    monkeypatch.setattr(
        providers, "_azure_client_factory", lambda **_kwargs: client, raising=False
    )
    return client


def _scenario_file(tmp_path: Path) -> Path:
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    return path


def _read(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


# --- recording ------------------------------------------------------------


def test_records_the_hash_and_foundry_metadata(
    tmp_path: Path,
    foundry_env: None,
    fake_foundry: _FakeFoundryClient,
) -> None:
    out = tmp_path / "cassette.json"

    code = record_cassette.main(
        ["--scenario", str(_scenario_file(tmp_path)), "--out", str(out)]
    )

    assert code == 0
    document = _read(out)
    assert document[DIGEST] == DIAGNOSIS
    assert document["captured_from"] == "azure_foundry"
    assert document["model"] == FOUNDRY_MODEL
    assert fake_foundry.calls[0]["model"] == FOUNDRY_MODEL


def test_model_label_overrides_the_recorded_model(
    tmp_path: Path,
    foundry_env: None,
    fake_foundry: _FakeFoundryClient,
) -> None:
    out = tmp_path / "cassette.json"

    record_cassette.main(
        [
            "--scenario",
            str(_scenario_file(tmp_path)),
            "--out",
            str(out),
            "--model",
            "label-for-review",
        ]
    )

    assert _read(out)["model"] == "label-for-review"


def test_merging_preserves_an_existing_entry(
    tmp_path: Path,
    foundry_env: None,
    fake_foundry: _FakeFoundryClient,
) -> None:
    other = "a" * 64
    out = tmp_path / "cassette.json"
    out.write_text(
        json.dumps(
            {
                "captured_from": "authored_fixture",
                "model": None,
                other: {"risk": "low"},
            }
        ),
        encoding="utf-8",
    )

    record_cassette.main(
        ["--scenario", str(_scenario_file(tmp_path)), "--out", str(out)]
    )

    document = _read(out)
    assert document[other] == {"risk": "low"}
    assert document[DIGEST] == DIAGNOSIS
    assert document["captured_from"] == "azure_foundry"


def test_a_duplicate_hash_is_overwritten_with_a_note(
    tmp_path: Path,
    foundry_env: None,
    fake_foundry: _FakeFoundryClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "cassette.json"
    out.write_text(json.dumps({DIGEST: {"risk": "low"}}), encoding="utf-8")

    record_cassette.main(
        ["--scenario", str(_scenario_file(tmp_path)), "--out", str(out)]
    )

    assert _read(out)[DIGEST] == DIAGNOSIS
    assert "overwr" in capsys.readouterr().out.lower()


def test_payload_can_arrive_on_stdin(
    tmp_path: Path,
    foundry_env: None,
    fake_foundry: _FakeFoundryClient,
) -> None:
    out = tmp_path / "cassette.json"

    code = record_cassette.main(
        ["--out", str(out)], stdin=io.StringIO(json.dumps(PAYLOAD))
    )

    assert code == 0
    assert _read(out)[DIGEST] == DIAGNOSIS


# --- refusals -------------------------------------------------------------


def test_missing_credentials_exit_two_and_write_nothing(
    tmp_path: Path,
    foundry_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    out = tmp_path / "cassette.json"

    code = record_cassette.main(
        ["--scenario", str(_scenario_file(tmp_path)), "--out", str(out)]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert not out.exists()
    assert "LLM_BASE_URL is not set" in captured.err
    assert "LLM_API_KEY is not set" in captured.err
    assert "LLM_MODEL is not set" in captured.err


def test_help_documents_the_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        record_cassette.main(["--help"])

    assert excinfo.value.code == 0
    printed = capsys.readouterr().out
    assert "--scenario" in printed
    assert "--out" in printed
    assert "--model" in printed
