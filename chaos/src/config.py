import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "chaos"
    version: str = "0.1.0"
    log_level: str = "INFO"
    cors_origins: list[str] = ["*"]


settings = Settings()


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


class ChaosConfig:
    """Runtime knobs, read when the process starts.

    Mutable and unfrozen: tests pin `CHAOS_DB_PATH` before importing the app,
    and operators may point the service at a different simulator.
    """

    def __init__(self) -> None:
        self.DB_PATH: str = _env("CHAOS_DB_PATH", "./data/chaos.db")
        self.SIMULATOR_URL: str = _env("SIMULATOR_URL", "http://kubeheal-sim:8083")
        self.SWEEP_INTERVAL_SECONDS: float = float(_env("SWEEP_INTERVAL_SECONDS", "5.0"))


config = ChaosConfig()
