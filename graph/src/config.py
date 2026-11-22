import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "graph"
    version: str = "0.1.0"
    log_level: str = "INFO"
    cors_origins: list[str] = ["*"]


settings = Settings()


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


class GraphConfig:
    """Runtime knobs, read per request.

    Mutable and unfrozen: tests flip `LOCAL_DEMO` on the shared object, and an
    operator may point the service at a different API or flow gateway.
    """

    def __init__(self) -> None:
        self.LOCAL_DEMO: bool = _env("LOCAL_DEMO", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        self.K8S_API_URL: str = _env("K8S_API_URL", "https://kubernetes.default.svc")
        self.HUBBLE_FLOWS_URL: str = _env(
            "HUBBLE_FLOWS_URL", "http://hubble-relay.kube-system.svc/flows"
        )
        self.HUBBLE_MAX_FLOWS: int = int(_env("HUBBLE_MAX_FLOWS", "1000"))
        self.SOURCE_TIMEOUT_SECONDS: float = float(_env("SOURCE_TIMEOUT_SECONDS", "10.0"))


config = GraphConfig()
