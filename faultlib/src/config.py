import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "faultlib"
    version: str = "0.1.0"
    log_level: str = "INFO"
    cors_origins: list[str] = ["*"]


settings = Settings()


class RuntimeConfig:
    """Env-seeded values read at call time; mutable for local overrides."""

    def __init__(self) -> None:
        self.CHAOS_URL: str = os.getenv("CHAOS_URL", "http://chaos:8082")


config = RuntimeConfig()
