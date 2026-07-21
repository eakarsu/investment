from datetime import timedelta
from ipaddress import ip_address
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Read from .env first, fall back to .env.local for backwards compat.
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    db_url: str = "postgresql+asyncpg://erolakarsu@localhost:5432/investment"
    host: str = "0.0.0.0"
    port: int = 8080

    # OpenRouter — used to narrate / explain each paper's algorithm run.
    # Standardized model: anthropic/claude-3-5-sonnet-20241022 (override via env).
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-3-5-sonnet-20241022"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # JWT authentication is deliberately short-lived and fixed to HS256.
    jwt_secret_key: str = "change-me-in-production-32chars!!!"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    jwt_issuer: str = "investment-paper-api"
    jwt_audience: str = "investment-paper-clients"

    # Alpha Vantage for real price data (free tier: 25 calls/day)
    alpha_vantage_api_key: str = ""

    # Environment + CORS — set CORS_ORIGINS to a comma-separated list in prod.
    environment: str = "development"
    cors_origins: str = "http://localhost:3000"
    market_data_allowed_hosts: str = ""
    market_data_max_age_seconds: int = 300
    future_event_tolerance_seconds: int = 300

    # Per-user AI rate limiting (calls / hour)
    ai_rate_limit_per_hour: int = 20

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    @property
    def market_data_host_list(self) -> list[str]:
        return [host.strip().lower() for host in self.market_data_allowed_hosts.split(",") if host.strip()]

    @property
    def market_data_max_age_seconds_delta(self) -> timedelta:
        return timedelta(seconds=self.market_data_max_age_seconds)

    @property
    def future_event_tolerance_delta(self) -> timedelta:
        return timedelta(seconds=self.future_event_tolerance_seconds)


settings = Settings()


INSECURE_DEFAULT_JWT = "change-me-in-production-32chars!!!"


def validate_runtime_config() -> None:
    """Reject unsafe production configuration before the service listens."""
    failures: list[str] = []
    if settings.jwt_algorithm != "HS256":
        failures.append("JWT_ALGORITHM must be HS256")
    if not 1 <= settings.jwt_expire_minutes <= 60:
        failures.append("JWT_EXPIRE_MINUTES must be between 1 and 60")
    if not settings.jwt_issuer.strip() or not settings.jwt_audience.strip():
        failures.append("JWT_ISSUER and JWT_AUDIENCE are required")
    if not 30 <= settings.market_data_max_age_seconds <= 3600:
        failures.append("MARKET_DATA_MAX_AGE_SECONDS must be between 30 and 3600")
    if not 0 <= settings.future_event_tolerance_seconds <= 600:
        failures.append("FUTURE_EVENT_TOLERANCE_SECONDS must be between 0 and 600")
    if not settings.cors_origin_list or "*" in settings.cors_origin_list:
        failures.append("CORS_ORIGINS must be an explicit allowlist")
    parsed = urlparse(settings.db_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    if settings.is_production:
        if len(settings.jwt_secret_key) < 32 or settings.jwt_secret_key == INSECURE_DEFAULT_JWT:
            failures.append("JWT_SECRET_KEY must be at least 32 non-placeholder characters")
        if not settings.market_data_host_list or any(
            host == "*" or "://" in host for host in settings.market_data_host_list
        ):
            failures.append("MARKET_DATA_ALLOWED_HOSTS must list hostnames")
        if parsed.scheme != "postgresql" or not parsed.hostname:
            failures.append("DB_URL must identify PostgreSQL")
        database_host = (parsed.hostname or "").rstrip(".").lower()
        loopback = database_host == "localhost"
        try:
            loopback = loopback or ip_address(database_host).is_loopback
        except ValueError:
            pass
        if loopback:
            failures.append("production DB_URL must identify an external database")
        if any(
            (parsed_origin := urlparse(origin)).scheme != "https" or not parsed_origin.hostname
            for origin in settings.cors_origin_list
        ):
            failures.append("production CORS_ORIGINS must use HTTPS")
    if failures:
        raise RuntimeError("Invalid runtime configuration: " + "; ".join(failures))
