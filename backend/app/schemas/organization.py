import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator

from app.core.config import get_settings


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc
    return value


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
    privacy_email: EmailStr | None = None

    _timezone = field_validator("timezone")(_validate_timezone)

    @classmethod
    def from_db(cls, raw: dict[str, Any] | None) -> "OrgSettings":
        return cls.model_validate(raw or {})

    def to_db(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# Fields that appear in consent texts: changing them publishes new consent versions.
LEGAL_FIELDS = frozenset({"legal_name", "inn", "legal_address", "privacy_email"})


class OrgSettingsUpdate(BaseModel):
    """Partial update: only the fields that were sent are changed."""

    collect_contact: bool | None = None
    contact_offer_text: str | None = Field(default=None, max_length=300)
    yandex_maps_url: HttpUrl | None = None
    two_gis_url: HttpUrl | None = None
    negative_threshold: int | None = Field(default=None, ge=1, le=5)
    timezone: str | None = None
    privacy_policy_url: HttpUrl | None = None
    legal_name: str | None = Field(default=None, max_length=300)
    inn: str | None = Field(default=None, pattern=r"^\d{10}(\d{2})?$")
    legal_address: str | None = Field(default=None, max_length=500)
    privacy_email: EmailStr | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_valid(cls, value: str | None) -> str | None:
        return _validate_timezone(value) if value is not None else None

    @field_validator("collect_contact", "negative_threshold", "timezone", mode="before")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # These settings always have a value: omit the field instead of sending null.
        if value is None:
            raise ValueError("Поле не может быть пустым")
        return value


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    settings: OrgSettingsUpdate | None = None


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    niche: str
    employee_title: str
    settings: OrgSettings
