from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Alera Backend API"
    app_version: str = "0.1.0"
    environment: str = "development"

    database_url: str | None = None
    sql_echo: bool = False

    fcm_enabled: bool = False
    firebase_project_id: str | None = None
    firebase_service_account_json: SecretStr | None = None

    device_heartbeat_stale_seconds: int = 150
    device_liveness_check_seconds: int = 30

    alera_jwt_secret: str | None = None
    alera_jwt_access_token_minutes: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
