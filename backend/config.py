import logging
import os
import sys

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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

    # JWT Auth — override JWT_SECRET_KEY in production!
    jwt_secret_key: str = "change-me-in-production-32chars!!!"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # Alpha Vantage for real price data (free tier: 25 calls/day)
    alpha_vantage_api_key: str = ""

    # Environment + CORS — set CORS_ORIGINS to a comma-separated list in prod.
    environment: str = "development"
    cors_origins: str = "http://localhost:3000"

    # Per-user AI rate limiting (calls / hour)
    ai_rate_limit_per_hour: int = 20

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")


settings = Settings()


INSECURE_DEFAULT_JWT = "change-me-in-production-32chars!!!"


def _warn_missing_keys() -> None:
    """Log clear warnings at startup for any missing critical config.
    In production, fail-fast on insecure defaults instead of merely warning.
    """
    if not settings.openrouter_api_key:
        logger.warning(
            "OPENROUTER_API_KEY is not set — /api/narrate/* calls will fail. "
            "Add it to your .env file."
        )

    if settings.jwt_secret_key == INSECURE_DEFAULT_JWT:
        if settings.is_production:
            logger.critical(
                "JWT_SECRET_KEY is using the insecure default in PRODUCTION. "
                "Refusing to start — set a strong random value in .env."
            )
            sys.exit(2)
        logger.warning(
            "JWT_SECRET_KEY is using the insecure default. "
            "Set a strong random value in .env for production."
        )

    if not settings.alpha_vantage_api_key:
        logger.info(
            "ALPHA_VANTAGE_API_KEY not set — portfolio will use cached/mock prices. "
            "Get a free key at https://www.alphavantage.co/support/#api-key"
        )


_warn_missing_keys()
