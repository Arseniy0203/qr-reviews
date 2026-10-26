"""Phone normalization to E.164 (+79991234567)."""

import re

_NON_DIGITS = re.compile(r"\D")
_E164 = re.compile(r"^\+[0-9]{10,15}$")


class InvalidPhoneError(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Normalize a user-typed phone to E.164; Russian formats get +7.

    "8 (999) 123-45-67", "+7 999 1234567", "999-123-45-67" -> "+79991234567".
    Other countries are accepted only with an explicit leading "+".
    """
    value = raw.strip()
    digits = _NON_DIGITS.sub("", value)
    if value.startswith("+"):
        normalized = f"+{digits}"
    elif len(digits) == 11 and digits[0] in "78":
        normalized = f"+7{digits[1:]}"
    elif len(digits) == 10 and digits[0] == "9":
        normalized = f"+7{digits}"
    else:
        raise InvalidPhoneError(raw)

    if normalized.startswith("+7") and len(normalized) != 12:
        raise InvalidPhoneError(raw)
    if not _E164.match(normalized):
        raise InvalidPhoneError(raw)
    return normalized
