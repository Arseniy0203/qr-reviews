import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.review import REVIEW_TEXT_MAX_LENGTH
from app.schemas.common import ORMModel


class ReviewSubmission(BaseModel):
    """Data of the public review form (step 1: rating, categories, text)."""

    rating: int = Field(ge=1, le=5)
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    text: str | None = Field(default=None, max_length=REVIEW_TEXT_MAX_LENGTH)

    @field_validator("text", mode="before")
    @classmethod
    def _blank_text_is_none(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            value = value.strip()
        return value or None


class ContactReviewOut(ORMModel):
    id: int
    created_at: datetime
    rating: int
    customer_name: str | None
    customer_phone: str | None
    pd_consent_at: datetime | None
    pd_consent_revoked_at: datetime | None
    marketing_consent_at: datetime | None
    marketing_consent_revoked_at: datetime | None


class RevokeRequest(BaseModel):
    phone: str = Field(min_length=5, max_length=30)
    # "pd": withdraw PD consent (contact is wiped); "marketing": stop advertising only.
    kind: Literal["pd", "marketing"] = "pd"


class RevokeResult(BaseModel):
    kind: Literal["pd", "marketing"]
    reviews_affected: int
    revoked_at: datetime
