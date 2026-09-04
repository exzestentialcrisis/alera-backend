from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Alera Backend API"
    app_version: str = "0.1.0"
    environment: str = "development"

    database_url: str | None = None
    sql_echo: bool = False

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
