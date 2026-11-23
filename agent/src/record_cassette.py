"""Record one live Azure AI Foundry diagnosis into a cassette.

Run from the service directory. The provider settings come from the root
`.env` (which `config` loads as defaults) or the environment:

    LLM_PROVIDER=azure-v1
    LLM_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1
    LLM_MODEL=gpt-4.1-mini
    LLM_API_KEY=<secret>

    python -m src.record_cassette --scenario payload.json \
        --out fixtures/diagnosis-cassette.json

The scenario document is the same four-key payload `nodes.diagnose` sends:
`scenario`, `blast_radius`, `catalog_entry`, `memory_context`. The payload may
also be piped on stdin. The entry is keyed by `prompt_sha256(payload)`, exactly
as `CassetteProvider` looks it up, and an existing cassette is merged, never
truncated. Missing credentials print every absent variable and exit 2 before
anything is written. The API key is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, TextIO

# `providers` imports its siblings flat, so make `src` importable when this
# module runs as `python -m src.record_cassette`.
_SRC = str(Path(__file__).resolve().parent)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import providers  # noqa: E402  (import after sys.path is fixed)

DEFAULT_OUT = Path("fixtures/diagnosis-cassette.json")
PAYLOAD_KEYS = ("scenario", "blast_radius", "catalog_entry", "memory_context")
CAPTURED_FROM = "azure_foundry"


class CassetteInputError(Exception):
    """The scenario document or the existing cassette cannot be used."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.record_cassette",
        description=(
            "Call the Azure AI Foundry diagnosis provider once and store the "
            "reply in a cassette, keyed by the canonical payload hash."
        ),
        epilog=(
            "The payload needs scenario, blast_radius, catalog_entry and "
            "memory_context. With no --scenario the payload is read from "
            "stdin. An existing cassette is merged; a duplicate hash is "
            "overwritten and reported."
        ),
    )
    parser.add_argument(
        "--scenario",
        metavar="PATH",
        help="JSON payload file; omit to read the same JSON from stdin",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=str(DEFAULT_OUT),
        help=f"cassette to merge into (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--model",
        metavar="LABEL",
        dest="model_label",
        help="label recorded as the cassette model (default: the deployment)",
    )
    return parser


def load_payload(scenario_path: str | None, stdin: TextIO) -> dict[str, Any]:
    """Read the four-key payload from a file or stdin."""
    if scenario_path:
        try:
            raw = Path(scenario_path).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise CassetteInputError(f"cannot read {scenario_path}: {exc}") from exc
    else:
        raw = stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CassetteInputError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CassetteInputError("payload must be a JSON object")
    missing = [key for key in PAYLOAD_KEYS if key not in payload]
    if missing:
        raise CassetteInputError(f"payload is missing: {', '.join(missing)}")
    return payload


def load_cassette(path: Path) -> dict[str, Any]:
    """Read an existing cassette for merging; a missing file starts empty."""
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CassetteInputError(f"cassette is unreadable: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise CassetteInputError(f"cassette must be a JSON object: {path}")
    return document


def write_cassette(path: Path, document: dict[str, Any]) -> None:
    """Write atomically, so an interrupted run never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2) + "\n"
    handle_fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    source = stdin if stdin is not None else sys.stdin

    try:
        payload = load_payload(args.scenario, source)
    except CassetteInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    missing = providers.foundry_missing_settings()
    if missing:
        for name in missing:
            print(f"{name} is not set", file=sys.stderr)
        return 2

    provider = providers.AzureFoundryProvider()
    diagnosis = provider.diagnose(payload)
    digest = providers.prompt_sha256(payload)

    out = Path(args.out).expanduser()
    try:
        document = load_cassette(out)
    except CassetteInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    overwritten = digest in document
    document[digest] = diagnosis
    document["captured_from"] = CAPTURED_FROM
    document["model"] = args.model_label or provider.model
    write_cassette(out, document)

    print(f"recorded {digest[:12]} from {CAPTURED_FROM} to {out}")
    if overwritten:
        print(f"overwrote existing entry {digest[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
