"""Diagnosis providers behind one seam (CONTRACT D7).

Four implementations, selected by `LLM_PROVIDER` (falling back to
`LLM_MODE`):

* `StubProvider` — the deterministic fixture, the default in tests and the
  offline demo. It delegates to `graph.stub_diagnosis` so the two cannot drift.
* `CassetteProvider` — replays an authored or recorded diagnosis keyed by the
  canonical hash of the payload it is given. No network.
* `OpenAIProvider` — the live OpenAI path. `openai` is imported inside
  `__init__`, so this module, the service and every test import without the
  package.
* `AzureFoundryProvider` — the live Azure AI Foundry path, reached through its
  OpenAI-compatible v1 surface: `base_url` and `api_key`, no `api-version`.
  It validates `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` before importing
  `openai` and takes the client from an injectable factory, so
  missing-credential messages and tests are identical either way. The
  `AZURE_OPENAI_*` names remain as aliases, never overriding `LLM_*`.

The cassette key is `prompt_sha256(payload)`: the SHA-256 of
`json.dumps(payload, sort_keys=True, separators=(",", ":"))`. That normalises
key order and whitespace only. List order, integer-versus-float and the exact
value of every field still matter: a cassette replays only when the context
handed to the provider is identical, not merely equivalent. `nodes.diagnose`
defines the payload (scenario, blast radius, catalogue entry, memory context)
and records the same hash in `diagnosis_provenance`.

`build_provider` fails closed: a mode it does not know raises `ValueError`
instead of silently falling back to the stub.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Protocol

from config import config as runtime_config

DEFAULT_MODEL = "gpt-4o-mini"
# `azure` and `foundry` are accepted for the same OpenAI-compatible surface.
FOUNDRY_MODES = ("azure", "azure-v1", "foundry")
# (primary LLM_* name, AZURE_OPENAI_* alias)
FOUNDRY_SETTINGS = (
    ("LLM_BASE_URL", "AZURE_OPENAI_ENDPOINT"),
    ("LLM_API_KEY", "AZURE_OPENAI_API_KEY"),
    ("LLM_MODEL", "AZURE_OPENAI_DEPLOYMENT"),
)
SHA256_HEX_LENGTH = 64
HASH_PREFIX_LENGTH = 12

# Tests and the cassette recorder replace this to avoid the SDK and the network.
_azure_client_factory: Callable[..., Any] | None = None

SYSTEM_PROMPT = (
    "You are KubeHeal's diagnosis stage. Given a chaos scenario, its blast "
    "radius, the fault catalogue entry and recent incident memory, reply with "
    "one JSON object and nothing else: causal_chain (list of strings), "
    "recommended_action (restart_deployment | stop_scenario | "
    "scale_deployment), action_target, risk (low | medium | high) and "
    "rationale (one sentence).\n"
    "action_target must always be '<namespace>/<deployment>' of the workload "
    "that carries the fault, for every action. Identify it from the scenario's "
    "target namespace and label selector; never use the scenario id.\n"
    "A pod kill is remediated by restart_deployment of that deployment: the "
    "ReplicaSet has already replaced the pod, and the restart is what clears "
    "the failed endpoints. Choose stop_scenario only when the fault is an "
    "injected experiment that cannot be cleared by acting on the workload, and "
    "scale_deployment only when capacity itself is the problem. Choose the "
    "action that fixes the cause, not the one that hides it. Use only the "
    "three listed actions and never invent a field."
)


class DiagnosisError(Exception):
    """A provider could not produce a diagnosis (miss, bad output, no key)."""


class DiagnosisProvider(Protocol):
    """What `nodes.diagnose` needs: one payload in, one diagnosis dict out."""

    model: str | None

    def diagnose(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def prompt_sha256(payload: Any) -> str:
    """Canonical hash used to key cassettes and to record provenance."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_diagnosis_text(raw: Any) -> dict[str, Any]:
    """Parse one model reply; anything but a JSON object raises."""
    text = _strip_fences(raw if isinstance(raw, str) else str(raw))
    error: Exception | None = None
    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except Exception as exc:
            error = exc
            continue
        if not isinstance(parsed, dict):
            raise DiagnosisError("diagnosis is not a JSON object")
        return parsed
    snippet = " ".join(text.split())[:120]
    raise DiagnosisError(f"unparseable diagnosis: {error}; reply was {snippet!r}")


def _json_candidates(text: str) -> list[str]:
    """The whole reply, then the first `{`..last `}` slice when one is wrapped.

    A model told to answer with one object still sometimes introduces it with a
    sentence. Failing the whole diagnosis over that (and then auto-stopping the
    scenario) is a worse outcome than reading the object it did return.
    """
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    return candidates


class StubProvider:
    """Deterministic, offline, and identical to `graph.stub_diagnosis`."""

    model: str | None = None

    def diagnose(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Lazy: `graph` imports `nodes`, which imports this module.
        from graph import stub_diagnosis

        return stub_diagnosis(**payload)


def _is_sha256(key: str) -> bool:
    return len(key) == SHA256_HEX_LENGTH and all(
        character in "0123456789abcdef" for character in key
    )


def _load_cassette(path: Path) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Read a cassette once: 64-hex keys are entries, every other key is metadata."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DiagnosisError(f"cassette unreadable: {path}: {exc}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DiagnosisError(f"cassette is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise DiagnosisError("cassette must be a JSON object")

    entries: dict[str, dict[str, Any]] = {}
    for key, value in document.items():
        if not _is_sha256(key):
            continue
        if not isinstance(value, dict):
            raise DiagnosisError(f"cassette entry {key[:HASH_PREFIX_LENGTH]} is not an object")
        entries[key] = value
    if not entries:
        raise DiagnosisError(f"cassette has no entries: {path}")

    model = document.get("model")
    return entries, model if isinstance(model, str) else None


class CassetteProvider:
    """Replay an authored recording; a miss is an error, never a guess."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._entries, self.model = _load_cassette(self.path)

    def diagnose(self, payload: dict[str, Any]) -> dict[str, Any]:
        digest = prompt_sha256(payload)
        entry = self._entries.get(digest)
        if entry is None:
            raise DiagnosisError(
                f"cassette miss for prompt sha256 {digest[:HASH_PREFIX_LENGTH]}"
            )
        return copy.deepcopy(entry)


class OpenAIProvider:
    """The live provider; requires a key and the optional `openai` package."""

    def __init__(self, model: str | None = None) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise DiagnosisError("OPENAI_API_KEY is not set")
        try:
            import openai
        except ImportError as exc:
            raise DiagnosisError("the openai package is not installed") from exc
        self.model = model or runtime_config.LLM_MODEL or DEFAULT_MODEL
        self._client = openai.OpenAI(api_key=api_key)

    def diagnose(self, payload: dict[str, Any]) -> dict[str, Any]:
        completion = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                },
            ],
        )
        output = completion.choices[0].message.content
        return parse_diagnosis_text(output)


def selected_mode(config: Any) -> Any:
    """`LLM_PROVIDER` when set, otherwise the older `LLM_MODE`."""
    return getattr(config, "LLM_PROVIDER", "") or getattr(config, "LLM_MODE", None)


def _foundry_setting(
    primary: str, alias: str, explicit: str | None = None
) -> str:
    """Resolve one setting: explicit, `LLM_*`, alias, config, alias config."""
    if explicit:
        return explicit
    return (
        os.environ.get(primary)
        or os.environ.get(alias)
        or str(getattr(runtime_config, primary, "") or "")
        or str(getattr(runtime_config, alias, "") or "")
    )


def foundry_missing_settings() -> list[str]:
    """The required settings that are absent, in validation order."""
    return [
        primary
        for primary, alias in FOUNDRY_SETTINGS
        if not _foundry_setting(primary, alias)
    ]


class AzureFoundryProvider:
    """Live Azure Foundry over the OpenAI-compatible v1 surface."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = _foundry_setting(
            "LLM_BASE_URL", "AZURE_OPENAI_ENDPOINT", base_url
        )
        self.api_key = _foundry_setting(
            "LLM_API_KEY", "AZURE_OPENAI_API_KEY", api_key
        )
        self.model = _foundry_setting(
            "LLM_MODEL", "AZURE_OPENAI_DEPLOYMENT", model
        )
        if not self.base_url:
            raise DiagnosisError("LLM_BASE_URL is not set")
        if not self.api_key:
            raise DiagnosisError("LLM_API_KEY is not set")
        if not self.model:
            raise DiagnosisError("LLM_MODEL is not set")
        client_kwargs = {"base_url": self.base_url, "api_key": self.api_key}
        if _azure_client_factory is not None:
            self._client = _azure_client_factory(**client_kwargs)
            return
        try:
            import openai
        except ImportError as exc:
            raise DiagnosisError("the openai package is not installed") from exc
        self._client = openai.OpenAI(**client_kwargs)

    def diagnose(self, payload: dict[str, Any]) -> dict[str, Any]:
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                temperature=0,
            )
            output = completion.choices[0].message.content
        except Exception as exc:
            raise DiagnosisError(str(exc) or exc.__class__.__name__) from exc
        return parse_diagnosis_text(output)


def build_provider(config: Any) -> DiagnosisProvider:
    """Select the provider named by `LLM_PROVIDER`/`LLM_MODE`; else fail."""
    mode = selected_mode(config)
    if mode == "stub":
        return StubProvider()
    if mode == "cassette":
        return CassetteProvider(config.CASSETTE_PATH)
    if mode == "openai":
        return OpenAIProvider(config.LLM_MODEL)
    if mode in FOUNDRY_MODES:
        return AzureFoundryProvider(
            base_url=getattr(config, "LLM_BASE_URL", None),
            api_key=getattr(config, "LLM_API_KEY", None),
            model=getattr(config, "LLM_MODEL", None),
        )
    raise ValueError(
        f"unknown LLM_PROVIDER {mode!r}: expected stub, cassette, openai, "
        "azure, azure-v1 or foundry"
    )
