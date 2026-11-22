"""Export this service's OpenAPI schema to `openapi.json`.

Run from the service directory:

    python -m src.export_openapi

The committed file is the schema of record: the dashboard generates its types
from it and cross-service tests assert the query strings they send.
"""

import json
import sys
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = SERVICE_ROOT / "openapi.json"

# `main` imports its siblings flat (`from config import settings`), so the src
# directory has to be importable when this module runs as `src.export_openapi`.
_SRC = str(Path(__file__).resolve().parent)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from main import app  # noqa: E402  (import after sys.path is fixed)


def build_schema() -> dict[str, Any]:
    """Return the application's OpenAPI schema."""
    return app.openapi()


def main() -> None:
    schema = build_schema()
    SCHEMA_PATH.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
