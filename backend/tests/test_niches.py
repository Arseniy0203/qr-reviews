import pytest
from pydantic import ValidationError

from app.schemas.organization import OrgSettings
from app.services.niches import UnknownNicheError, get_niche, load_niches


def test_beauty_niche_matches_spec() -> None:
    beauty = get_niche("beauty")
    assert beauty.employee_title == "мастер"
    assert [c.name for c in beauty.categories] == [
        "Качество работы",
        "Мастер",
        "Запись",
        "Стерильность",
        "Атмосфера",
        "Цена",
    ]


def test_all_niches_load_and_generic_exists() -> None:
    niches = load_niches()
    assert {"beauty", "generic"} <= niches.keys()
    assert niches["generic"].employee_title == "сотрудник"


def test_unknown_niche() -> None:
    with pytest.raises(UnknownNicheError):
        get_niche("spaceport")


def test_org_settings_defaults() -> None:
    settings = OrgSettings.from_db(None)
    assert settings.negative_threshold == 3
    assert settings.timezone == "Europe/Moscow"
    assert settings.collect_contact is False


def test_contact_step_needs_complete_legal_details() -> None:
    legal = {
        "legal_name": "ООО «Лаванда»",
        "inn": "7701234567",
        "legal_address": "Москва",
        "privacy_email": "pd@lavanda.ru",
    }
    assert OrgSettings(collect_contact=True, **legal).contact_step_enabled
    assert not OrgSettings(collect_contact=False, **legal).contact_step_enabled
    partial = OrgSettings(collect_contact=True, **{**legal, "inn": None})
    assert not partial.contact_step_enabled
    assert partial.missing_legal_fields() == ["inn"]


def test_org_settings_validation() -> None:
    with pytest.raises(ValidationError):
        OrgSettings(negative_threshold=6)
    with pytest.raises(ValidationError):
        OrgSettings(timezone="Nowhere/City")
