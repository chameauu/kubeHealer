"""Diagnosis providers behind one seam (CONTRACT D7).

Three implementations, selected by `LLM_MODE`:

* `StubProvider` — the deterministic fixture, the default in tests and the
  offline demo. It delegates to `graph.stub_diagnosis` so the two cannot drift.
* `CassetteProvider` — replays an authored or recorded diagnosis keyed by the
  canonical hash of the payload it is given. No network.
* `OpenAIProvider` — the live path. `openai` is imported inside `__init__`, so
  this module, the service and every test import without the package.

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
from typing import Any, Protocol

from config import config as runtime_config

DEFAULT_MODEL = "gpt-4o-mini"
SHA256_HEX_LENGTH = 64
HASH_PREFIX_LENGTH = 12

SYSTEM_PROMPT = (
    "You are KubeHeal's diagnosis stage. Given a chaos scenario, its blast "
    "radius, the fault catalogue entry and recent incident memory, reply with "
    "one JSON object and nothing else: causal_chain (list of strings), "
    "recommended_action (restart_deployment | stop_scenario | "
    "scale_deployment), action_target (namespace/name or scenario id), risk "
    "(low | medium | high) and rationale (one sentence). Use only the three "
    "listed actions and never invent a field."
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
    try:
        parsed = json.loads(text)
    except Exception as exc:
        raise DiagnosisError(f"unparseable diagnosis: {exc}") from exc
    if not isinstance(parsed, dict):
        raise DiagnosisError("diagnosis is not a JSON object")
    return parsed


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


def build_provider(config: Any) -> DiagnosisProvider:
    """Select the provider named by `config.LLM_MODE`; anything else fails."""
    mode = getattr(config, "LLM_MODE", None)
    if mode == "stub":
        return StubProvider()
    if mode == "cassette":
        return CassetteProvider(config.CASSETTE_PATH)
    if mode == "openai":
        return OpenAIProvider(config.LLM_MODEL)
    raise ValueError(f"unknown LLM_MODE {mode!r}: expected stub, cassette or openai")
