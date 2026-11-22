"""The exported OpenAPI file is part of the contract.

It must match the live app, document `/health`, and be committed — a stale
`openapi.json` is a silent contract drift for every client generated from it.
"""

import json
from pathlib import Path

import export_openapi
from main import app

SERVICE_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = SERVICE_ROOT / "openapi.json"


def test_schema_matches_app() -> None:
    assert export_openapi.build_schema() == app.openapi()


def test_schema_documents_health() -> None:
    schema = export_openapi.build_schema()

    assert "/health" in schema["paths"]
    assert schema["info"]["title"] == "chaos"


def test_committed_schema_is_current() -> None:
    assert SCHEMA_FILE.exists(), "run `python -m src.export_openapi` from the service directory"

    committed = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))

    assert committed == export_openapi.build_schema(), (
        "openapi.json is stale: re-run `python -m src.export_openapi`"
    )
