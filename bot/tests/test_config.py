from bot.config import BotSettings


def test_empty_token_means_disabled() -> None:
    settings = BotSettings(telegram_bot_token="", internal_api_secret="x")
    assert settings.telegram_bot_token is None


def test_backend_url_normalized() -> None:
    settings = BotSettings(internal_api_secret="x", backend_internal_url="http://backend:8000/")
    assert settings.backend_internal_url == "http://backend:8000"
