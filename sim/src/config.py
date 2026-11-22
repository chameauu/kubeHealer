import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "sim"
    version: str = "0.1.0"
    log_level: str = "INFO"
    cors_origins: list[str] = ["*"]


settings = Settings()


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


class SimulatorConfig:
    """Mutable simulator knobs, read at call time rather than import time.

    Tests and operators adjust these between runs, so the object must stay
    mutable and un-frozen.
    """

    def __init__(self) -> None:
        self.LIFECYCLE_SPEED: float = float(_env("LIFECYCLE_SPEED", "1.0"))
        self.VOLUME_QUOTA: int = int(_env("VOLUME_QUOTA", "50"))
        self.SUBNET_QUOTA: int = int(_env("SUBNET_QUOTA", "20"))
        self.INSTANCE_QUOTA: int = int(_env("INSTANCE_QUOTA", "30"))


config = SimulatorConfig()
