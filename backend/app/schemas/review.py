import uuid

from pydantic import BaseModel, Field, field_validator

from app.models.review import REVIEW_TEXT_MAX_LENGTH


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
