from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "chaos"
    version: str = "0.1.0"
    log_level: str = "INFO"
    cors_origins: list[str] = ["*"]


settings = Settings()
