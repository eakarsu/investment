from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read from .env first, fall back to .env.local for backwards compat.
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    db_url: str = "postgresql+asyncpg://erolakarsu@localhost:5432/investment"
    host: str = "0.0.0.0"
    port: int = 8080

    # OpenRouter — used to narrate / explain each paper's algorithm run.
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-3.5-sonnet"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"


settings = Settings()
