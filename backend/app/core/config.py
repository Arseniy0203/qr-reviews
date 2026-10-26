"""Application settings, loaded exclusively from environment variables."""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_PREFIX = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # --- General ---
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # --- Secrets ---
    secret_key: SecretStr
    hash_salt: SecretStr
    internal_api_secret: SecretStr

    # --- Infrastructure ---
    database_url: str
    redis_url: str = "redis://redis:6379/0"

    # --- URLs ---
    public_base_url: str = "http://localhost:8080"
    dashboard_base_url: str = "http://localhost:8080"
    cors_origins: str = ""

    # --- Auth ---
    access_token_ttl_minutes: int = Field(default=15, gt=0)
    refresh_token_ttl_days: int = Field(default=30, gt=0)
    cookie_secure: bool = False
    cookie_domain: str | None = None
    invitation_ttl_days: int = Field(default=7, gt=0)

    # --- Telegram ---
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str = ""
    telegram_link_code_ttl_minutes: int = Field(default=15, gt=0)

    # --- Personal data (152-FZ) ---
    # Details of the service company processing PD on behalf of salons (named in consents).
    pd_processor_details: str = ""

    # --- Analytics (Russian services only; empty = disabled) ---
    yandex_metrika_id: str = ""

    # --- Organization defaults ---
    default_timezone: str = "Europe/Moscow"

    # --- Antifraud ---
    af_device_max_per_point: int = Field(default=1, ge=1)
    af_device_window_hours: int = Field(default=24, gt=0)
    af_ip_soft_threshold: int = Field(default=5, ge=1)
    af_ip_hard_threshold: int = Field(default=20, ge=1)
    af_ip_window_hours: int = Field(default=24, gt=0)
    af_burst_window_minutes: int = Field(default=15, gt=0)
    af_burst_point_max: int = Field(default=10, ge=1)
    af_burst_employee_max: int = Field(default=15, ge=1)
    af_simhash_min_length: int = Field(default=30, ge=1)
    af_simhash_max_distance: int = Field(default=3, ge=0, le=64)
    af_simhash_history_size: int = Field(default=500, ge=1)
    af_simhash_ttl_days: int = Field(default=30, gt=0)

    # --- Rate limits (per IP) ---
    rate_limit_login_max: int = Field(default=30, ge=1)
    rate_limit_login_window_seconds: int = Field(default=300, gt=0)
    rate_limit_public_form_max: int = Field(default=60, ge=1)
    rate_limit_public_form_window_seconds: int = Field(default=3600, gt=0)

    @field_validator("public_base_url", "dashboard_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("cookie_domain", mode="before")
    @classmethod
    def _empty_domain_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("telegram_bot_token", mode="before")
    @classmethod
    def _empty_token_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("default_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        if self.af_ip_hard_threshold < self.af_ip_soft_threshold:
            raise ValueError("AF_IP_HARD_THRESHOLD must be >= AF_IP_SOFT_THRESHOLD")
        if self.app_env == "prod":
            for name in ("secret_key", "hash_salt", "internal_api_secret"):
                secret: SecretStr = getattr(self, name)
                value = secret.get_secret_value()
                if value.startswith(PLACEHOLDER_PREFIX) or len(value) < 32:
                    raise ValueError(f"{name.upper()} must be a random string of 32+ chars in prod")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in prod")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from env
