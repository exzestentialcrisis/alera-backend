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

    sms_enabled: bool = False
    semaphore_api_key: SecretStr | None = None
    semaphore_sender_name: str | None = None
    semaphore_api_url: str = "https://api.semaphore.co/api/v4/messages"
    sms_timeout_seconds: float = 10.0

    device_heartbeat_stale_seconds: int = 150
    device_liveness_check_seconds: int = 30
    watch_not_worn_grace_seconds: int = 180

    inactivity_timezone: str = "Asia/Manila"
    inactivity_threshold_hours: int = 8
    inactivity_day_start_hour: int = 6
    inactivity_day_end_hour: int = 22
    activity_step_stale_seconds: int = 900

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-flash"
    ai_insights_enabled: bool = False

    alera_jwt_secret: str | None = None
    alera_jwt_access_token_minutes: int = 60

    reminder_lifecycle_secret: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str | None = None
    supabase_secret_key: SecretStr | None = None
    supabase_patient_photo_bucket: str = "patient-profile-photos"


@lru_cache
def get_settings() -> Settings:
    return Settings()
