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
    watch_not_worn_grace_seconds: int = 180

    inactivity_timezone: str = "Asia/Manila"
    inactivity_threshold_hours: int = 8
    inactivity_day_start_hour: int = 6
    inactivity_day_end_hour: int = 22
    activity_step_stale_seconds: int = 900

    alera_jwt_secret: str | None = None
    alera_jwt_access_token_minutes: int = 60

    reminder_lifecycle_secret: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
