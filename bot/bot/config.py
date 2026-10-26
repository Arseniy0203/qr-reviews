"""Bot settings, loaded from environment variables."""

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    telegram_bot_token: SecretStr | None = None
    backend_internal_url: str = "http://backend:8000"
    internal_api_secret: SecretStr
    log_level: str = "INFO"

    @field_validator("telegram_bot_token", mode="before")
    @classmethod
    def _empty_token_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("backend_internal_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


@lru_cache
def get_settings() -> BotSettings:
    return BotSettings()  # type: ignore[call-arg]  # values come from env
