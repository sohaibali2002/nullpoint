"""Typed application settings. Every external dependency is optional in dev.

Nothing here reads a secret at import time beyond what is present in the environment;
`Settings` is constructed once and cached. Tests override via environment variables
before importing the app.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Core
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "Nullpoint"
    app_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    secret_key: SecretStr = SecretStr("dev-insecure-secret-change-me-32-chars-minimum")
    access_token_ttl_minutes: int = 720
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Storage
    database_url: str = "sqlite:///./nullpoint.db"
    redis_url: str = "redis://localhost:6379/0"
    lake_root: Path = Path("./.lake")
    data_cache_ttl_hours: int = 24
    data_read_only: bool = True

    # Agents / LLM
    anthropic_api_key: SecretStr | None = None
    llm_provider: Literal["anthropic", "mock"] = "mock"
    model_orchestrator: str = "claude-opus-5-5"
    model_worker: str = "claude-sonnet-5-5"
    model_cheap: str = "claude-haiku-5-5"
    model_skeptic: str = "claude-opus-5-5"
    llm_monthly_budget_usd: float = 250.0
    sprint_budget_usd: float = 8.0
    sprint_wall_clock_minutes: int = 45
    max_agent_iterations: int = 3
    max_strategies_per_sprint: int = 6

    # Data providers (all free tiers)
    yfinance_enabled: bool = True
    stooq_enabled: bool = True
    fred_api_key: SecretStr | None = None
    alphavantage_api_key: SecretStr | None = None
    tiingo_api_key: SecretStr | None = None
    polygon_api_key: SecretStr | None = None
    alpaca_api_key: SecretStr | None = None
    alpaca_api_secret: SecretStr | None = None
    binance_api_key: SecretStr | None = None
    binance_api_secret: SecretStr | None = None
    sec_edgar_user_agent: str = "Nullpoint research contact@example.com"
    kaggle_key: SecretStr | None = None

    # Paper trading
    alpaca_paper_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_paper: bool = True
    ccxt_testnet: bool = True

    # Alerts
    alert_email_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    alert_email_to: str | None = None
    slack_webhook_url: SecretStr | None = None
    discord_webhook_url: SecretStr | None = None

    # Observability
    sentry_dsn: SecretStr | None = None
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "nullpoint-api"

    # Auth / billing
    auth_mode: Literal["local", "clerk"] = "local"
    clerk_jwks_url: str | None = None
    clerk_issuer: str | None = None
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None

    # Execution safety
    kill_switch: bool = False
    live_trading_enabled: bool = False  # intentionally unsupported; kept for auditability

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def anthropic_key(self) -> str | None:
        return self.anthropic_api_key.get_secret_value() if self.anthropic_api_key else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
