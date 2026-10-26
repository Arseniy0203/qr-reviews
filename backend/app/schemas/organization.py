from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.core.config import get_settings


class OrgSettings(BaseModel):
    """Per-organization settings stored in organizations.settings (JSONB)."""

    model_config = ConfigDict(extra="ignore")

    collect_contact: bool = False
    # None -> the niche's contact_offer_default is shown.
    contact_offer_text: str | None = Field(default=None, max_length=300)
    yandex_maps_url: HttpUrl | None = None
    two_gis_url: HttpUrl | None = None
    # Ratings <= threshold trigger a Telegram alert.
    negative_threshold: int = Field(default=3, ge=1, le=5)
    # All day/week/month stats are grouped in this timezone.
    timezone: str = Field(default_factory=lambda: get_settings().default_timezone)
    # None -> built-in privacy policy page.
    privacy_policy_url: HttpUrl | None = None

    # Operator details required by 152-FZ in consent texts (the salon is the PD operator).
    legal_name: str | None = Field(default=None, max_length=300)
    inn: str | None = Field(default=None, pattern=r"^\d{10}(\d{2})?$")
    legal_address: str | None = Field(default=None, max_length=500)
    privacy_email: str | None = Field(default=None, max_length=320)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value

    @classmethod
    def from_db(cls, raw: dict[str, Any] | None) -> "OrgSettings":
        return cls.model_validate(raw or {})

    def to_db(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
