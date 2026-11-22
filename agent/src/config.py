"""Configuration, parsed once at startup.

`settings` keeps the service identity every service shares. `config` is the
mutable runtime view: the store paths, backend URLs and the two timeouts.
Module-level singletons read it, and an operator may replace it in place
without rebuilding the app.
"""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


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
        self.LLM_MODEL: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.CASSETTE_PATH: str = os.environ.get(
            "AGENT_CASSETTE_PATH", "fixtures/diagnosis-cassette.json"
        )
        self.APPROVAL_TIMEOUT_SECONDS: int = int(
            os.environ.get("APPROVAL_TIMEOUT_SECONDS", "300")
        )
        self.VERIFY_TIMEOUT_SECONDS: int = int(
            os.environ.get("VERIFY_TIMEOUT_SECONDS", "60")
        )


settings = Settings()
config = RuntimeConfig()
