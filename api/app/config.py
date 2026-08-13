from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MarketStore POS API"
    api_prefix: str = "/api/v1"
    api_port: int = 8000
    database_url: str = "postgresql+psycopg://marketstore:marketstore@localhost:5432/marketstore"
    secret_key: str = "change-this-secret-key"
    access_token_expire_minutes: int = 60 * 24 * 7
    password_reset_code_minutes: int = 10
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "MarketStore POS"
    smtp_use_tls: bool = True
    password_reset_cooldown_seconds: int = 60
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings():
    return Settings()
