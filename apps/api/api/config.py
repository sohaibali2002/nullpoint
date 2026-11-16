"""Typed application settings. Every external dependency is optional in dev.

Nothing here reads a secret at import time beyond what is present in the environment;
`Settings` is constructed once and cached. Tests override via environment variables
before importing the app.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Kept local to avoid importing the agents package (and its API/DB imports) during
# configuration loading.  The adapter has the same value in agents.llm.
OPENROUTER_FREE_MODEL = "openrouter/free"


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
    sandbox_backend: Literal["auto", "docker", "local"] = "auto"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Storage
    database_url: str = "sqlite:///./nullpoint.db"
    redis_url: str = "redis://localhost:6379/0"
    lake_root: Path = Path("./.lake")
    data_cache_ttl_hours: int = 24
    data_read_only: bool = True
    allow_network: bool = True

    # Agents / LLM.  Any OpenAI-compatible endpoint works through LLM_PROVIDER=custom
    # plus LLM_BASE_URL; named providers pre-fill their public base URL.
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    mistral_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    llm_api_key: SecretStr | None = None
    # `auto` chooses OpenRouter when OPENROUTER_API_KEY is present, then the other
    # provider keys, and finally the deterministic mock.  Explicit values always win.
    llm_provider: str = "auto"
    llm_base_url: str | None = None
    llm_timeout_seconds: float = Field(default=90.0, ge=5, le=600)
    # `auto` resolves to the provider default below.  OpenRouter defaults are free models;
    # set these explicitly to use paid models or a specific free model.
    model_orchestrator: str = "auto"
    model_worker: str = "auto"
    model_cheap: str = "auto"
    model_skeptic: str = "auto"
    llm_monthly_budget_usd: float = 250.0
    sprint_budget_usd: float = 8.0
    sprint_wall_clock_minutes: int = 45
    max_agent_iterations: int = 3
    max_strategies_per_sprint: int = 6
    # 0 means auto-detect from CPU/cgroup limits; NULLPOINT_MAX_WORKERS overrides at runtime.
    max_workers: int = Field(default=0, ge=0, le=64)
    job_runner: Literal["auto", "arq", "subprocess", "sync"] = "auto"
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_stale_minutes: int = Field(default=120, ge=5, le=1440)
    auto_create_schema: bool = True
    min_paper_days: int = Field(default=30, ge=1, le=365)
    artifacts_dir: Path = Path("artifacts")

    # Research: Voyage knowledge base + web search (all optional; offline fallback is real)
    voyage_api_key: SecretStr | None = None
    voyage_model: str = "voyage-3-large"
    voyage_base_url: str = "https://api.voyageai.com/v1/embeddings"
    knowledge_chunk_chars: int = Field(default=1400, ge=200, le=8000)
    tavily_api_key: SecretStr | None = None
    exa_api_key: SecretStr | None = None
    firecrawl_api_key: SecretStr | None = None
    # Comma-separated so a plain .env value works without JSON decoding.
    web_search_providers: str = "tavily,exa,firecrawl"
    web_search_max_results: int = Field(default=6, ge=1, le=20)
    web_search_timeout_seconds: float = Field(default=25.0, ge=5, le=120)
    web_search_cache_ttl_seconds: float = Field(default=900.0, ge=0, le=86400)

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
    engine_sandbox: bool = False  # production must be true; runs every backtest in the sandbox

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @model_validator(mode="after")
    def _resolve_llm_profile(self) -> Settings:
        """Resolve provider and model-slot defaults before production guards run."""
        provider = self.llm_provider.strip().lower()
        if provider == "auto":
            # Prefer the free OpenRouter path.  The other keys preserve existing
            # single-provider deployments; no key means the honest offline mock.
            def configured(secret: SecretStr | None) -> bool:
                return bool(secret and secret.get_secret_value().strip())

            if configured(self.openrouter_api_key):
                provider = "openrouter"
            elif configured(self.anthropic_api_key):
                provider = "anthropic"
            elif configured(self.openai_api_key):
                provider = "openai"
            else:
                provider = "mock"
            self.llm_provider = provider

        defaults = {
            "openrouter": OPENROUTER_FREE_MODEL,
            "anthropic": "claude-opus-5-5",
            "openai": "gpt-4o-mini",
            "mock": "mock-scripted",
        }
        default_model = defaults.get(provider, "gpt-4o-mini")
        # The cheap slot is deliberately the same free router under OpenRouter; the
        # router already chooses a smaller/cheaper model when the task is simple.
        for field in ("model_orchestrator", "model_worker", "model_cheap", "model_skeptic"):
            if not getattr(self, field) or getattr(self, field).strip().lower() == "auto":
                setattr(self, field, default_model)
        return self

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.app_env == "production":
            if self.secret_key.get_secret_value() == "dev-insecure-secret-change-me-32-chars-minimum":
                raise ValueError(
                    "refusing to boot in production with the default JWT secret; "
                    "set SECRET_KEY to a unique 32+ character value"
                )
            if self.sandbox_backend == "local":
                raise ValueError(
                    "refusing to boot in production with the local sandbox; set SANDBOX_BACKEND=docker"
                )
            if self.auto_create_schema:
                raise ValueError(
                    "refusing to create schema in production; run `alembic upgrade head` and set "
                    "AUTO_CREATE_SCHEMA=false"
                )
            if not self.engine_sandbox:
                raise ValueError(
                    "refusing to run agent-authored strategies in-process in production; "
                    "set ENGINE_SANDBOX=true"
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def anthropic_key(self) -> str | None:
        return self.anthropic_api_key.get_secret_value() if self.anthropic_api_key else None

    @property
    def llm_key(self) -> str | None:
        """The key for the selected provider, with LLM_API_KEY as the generic override."""
        generic = self.llm_api_key.get_secret_value().strip() if self.llm_api_key else ""
        if generic:
            return generic
        provider = self.llm_provider.strip().lower()
        mapping = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "openrouter": self.openrouter_api_key,
            "groq": self.groq_api_key,
            "deepseek": self.deepseek_api_key,
            "mistral": self.mistral_api_key,
            "xai": self.xai_api_key,
            "gemini": self.gemini_api_key,
            "google": self.gemini_api_key,
        }
        secret = mapping.get(provider)
        value = secret.get_secret_value().strip() if secret else ""
        return value or None

    @property
    def voyage_key(self) -> str | None:
        return self.voyage_api_key.get_secret_value() if self.voyage_api_key else None

    @property
    def search_providers(self) -> list[str]:
        return [part.strip().lower() for part in self.web_search_providers.split(",") if part.strip()]

    @property
    def search_keys(self) -> dict[str, str | None]:
        return {
            "tavily": self.tavily_api_key.get_secret_value() if self.tavily_api_key else None,
            "exa": self.exa_api_key.get_secret_value() if self.exa_api_key else None,
            "firecrawl": self.firecrawl_api_key.get_secret_value() if self.firecrawl_api_key else None,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
