import pytest
from pydantic import ValidationError

from app.models import REVIEW_TEXT_MAX_LENGTH
from app.schemas.review import ReviewSubmission
from app.services.phone import InvalidPhoneError, normalize_phone


@pytest.mark.parametrize(
    "raw",
    ["8 (999) 123-45-67", "+7 999 1234567", "+7(999)123-45-67", "79991234567", "999-123-45-67"],
)
def test_russian_formats_normalize_to_same_number(raw: str) -> None:
    assert normalize_phone(raw) == "+79991234567"


def test_foreign_number_with_plus_kept() -> None:
    assert normalize_phone("+375 29 123-45-67") == "+375291234567"


@pytest.mark.parametrize("raw", ["", "12345", "8 999 123", "+7 999 123 45 678", "abc", "+1 23"])
def test_invalid_phones_rejected(raw: str) -> None:
    with pytest.raises(InvalidPhoneError):
        normalize_phone(raw)


def test_review_text_length_limit() -> None:
    ReviewSubmission(rating=5, text="а" * REVIEW_TEXT_MAX_LENGTH)
    with pytest.raises(ValidationError):
        ReviewSubmission(rating=5, text="а" * (REVIEW_TEXT_MAX_LENGTH + 1))


def test_blank_text_becomes_none_and_rating_bounds() -> None:
    assert ReviewSubmission(rating=1, text="   ").text is None
    with pytest.raises(ValidationError):
        ReviewSubmission(rating=0)
