"""Configuration, parsed once at startup.

`settings` keeps the service identity every service shares. `config` is the
mutable runtime view: the store paths, backend URLs and the two timeouts.
Module-level singletons read it, and an operator may replace it in place
without rebuilding the app.
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / ".env"


def _load_dotenv(path: Path) -> None:
    """Best-effort `.env` defaults: existing environment variables always win.

    No dependency and no failure on a missing or malformed file. A value may be
    quoted; an unquoted value loses a trailing ` # comment`. Nothing is logged,
    because the file holds a key.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value[:1] in ("'", '"'):
            closing = value.find(value[0], 1)
            value = value[1:closing] if closing != -1 else value[1:]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if name:
            os.environ.setdefault(name, value)


_load_dotenv(ENV_PATH)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "agent"
    version: str = "0.1.0"
    log_level: str = "INFO"
    cors_origins: list[str] = ["*"]


class RuntimeConfig:
    """Mutable runtime configuration read from the environment."""

    def __init__(self) -> None:
        self.AGENT_DB_PATH: str = os.environ.get("AGENT_DB_PATH", "./data/memory.db")
        self.CHECKPOINT_PATH: str = os.environ.get(
            "AGENT_CHECKPOINT_PATH", "./data/checkpoints.db"
        )
        self.GRAPH_URL: str = os.environ.get("GRAPH_URL", "http://graph:8080")
        self.FAULTLIB_URL: str = os.environ.get("FAULTLIB_URL", "http://faultlib:8081")
        self.CHAOS_URL: str = os.environ.get("CHAOS_URL", "http://chaos:8082")
        self.LLM_MODE: str = os.environ.get("LLM_MODE", "stub")
        self.LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "")
        self.LLM_MODEL: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.LLM_BASE_URL: str = os.environ.get("LLM_BASE_URL", "")
        self.LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
        self.CASSETTE_PATH: str = os.environ.get(
            "AGENT_CASSETTE_PATH", "fixtures/diagnosis-cassette.json"
        )
        # Legacy aliases for the OpenAI-compatible v1 surface; the LLM_* names
        # above win whenever both are present.
        self.AZURE_OPENAI_ENDPOINT: str = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        self.AZURE_OPENAI_API_KEY: str = os.environ.get("AZURE_OPENAI_API_KEY", "")
        self.AZURE_OPENAI_DEPLOYMENT: str = os.environ.get(
            "AZURE_OPENAI_DEPLOYMENT", ""
        )
        self.APPROVAL_TIMEOUT_SECONDS: int = int(
            os.environ.get("APPROVAL_TIMEOUT_SECONDS", "300")
        )
        self.VERIFY_TIMEOUT_SECONDS: int = int(
            os.environ.get("VERIFY_TIMEOUT_SECONDS", "60")
        )


settings = Settings()
config = RuntimeConfig()
