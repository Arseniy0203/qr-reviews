import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = {
    "secret_key": "s" * 40,
    "hash_salt": "h" * 40,
    "internal_api_secret": "i" * 40,
    "database_url": "postgresql+asyncpg://u:p@db/x",
}


def test_prod_rejects_placeholder_secrets() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(**{**BASE, "app_env": "prod", "cookie_secure": True, "secret_key": "change-me"})


def test_prod_requires_secure_cookie() -> None:
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(**{**BASE, "app_env": "prod", "cookie_secure": False})


def test_prod_accepts_valid_config() -> None:
    settings = Settings(**{**BASE, "app_env": "prod", "cookie_secure": True})
    assert settings.is_prod


def test_ip_hard_threshold_not_below_soft() -> None:
    with pytest.raises(ValidationError, match="AF_IP_HARD_THRESHOLD"):
        Settings(**{**BASE, "af_ip_soft_threshold": 10, "af_ip_hard_threshold": 5})


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown timezone"):
        Settings(**{**BASE, "default_timezone": "Mars/Olympus"})


def test_cors_origins_parsed_and_urls_normalized() -> None:
    settings = Settings(
        **{**BASE, "cors_origins": "http://a.ru, http://b.ru", "public_base_url": "http://x.ru/"}
    )
    assert settings.cors_origin_list == ["http://a.ru", "http://b.ru"]
    assert settings.public_base_url == "http://x.ru"
