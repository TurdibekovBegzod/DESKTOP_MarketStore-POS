from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MarketStore POS API"
    api_prefix: str = "/api/v1"
    api_port: int = 8000
    database_url: str = "postgresql+psycopg://marketstore:marketstore@localhost:5432/marketstore"
    secret_key: str = "change-this-secret-key"
    access_token_expire_minutes: int = 60 * 24 * 7
    password_reset_code_minutes: int = 3
    signup_verification_code_minutes: int = 3
    signup_verification_resend_seconds: int = 180
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
    # PostgreSQL stores sync data; Redis wakes SSE clients across API workers.
    sync_events_redis_url: str | None = "redis://localhost:6379/2"
    github_repo: str = "TurdibekovBegzod/DESKTOP_MarketStore-POS"
    github_token: str | None = None
    # Shared secret the release workflow presents when it tells us a new build
    # was published. Unset means the ping endpoint is disabled.
    release_ping_secret: str | None = None
    release_poll_seconds: int = 600
    app_releases_dir: str = "releases"
    ngrok_domain: str | None = None
    trusted_hosts: str = "localhost,127.0.0.1,testserver,api"
    # Bearer token the metrics scraper must present. The tunnel exposes every
    # path, so an unauthenticated /metrics would publish our traffic shape to
    # anyone who guesses the URL. Unset means the endpoint does not exist.
    metrics_token: str | None = None
    # Emergency/control-plane account. It is intentionally not stored in the
    # users table, so tenant data and ordinary login flows can never grant this
    # privilege. Leave the password empty to disable the control panel.
    superadmin_username: str = "superadmin"
    superadmin_password: str | None = None
    superadmin_token_expire_minutes: int = 15

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def resolved_trusted_hosts(self) -> list[str]:
        hosts = [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]
        if self.ngrok_domain:
            value = self.ngrok_domain.strip()
            parsed = urlparse(value if "://" in value else f"https://{value}")
            if parsed.hostname:
                hosts.append(parsed.hostname)
        return list(dict.fromkeys(hosts))


@lru_cache
def get_settings():
    return Settings()
