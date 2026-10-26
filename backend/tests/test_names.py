import pytest

from app.services.names import first_name, genitive_first_name


@pytest.mark.parametrize(
    ("name", "genitive"),
    [
        ("Анна", "Анны"),
        ("Ольга", "Ольги"),
        ("Екатерина", "Екатерины"),
        ("Марина", "Марины"),
        ("Мария", "Марии"),
        ("Наталья", "Натальи"),
        ("Дмитрий", "Дмитрия"),
        ("Алексей", "Алексея"),
        ("Игорь", "Игоря"),
        ("Иван", "Ивана"),
        ("Никита", "Никиты"),
        ("Илья", "Ильи"),
        ("Павел", "Павла"),
        ("Пётр", "Петра"),
        ("Любовь", "Любови"),
        ("Айгуль", "Айгуль"),
        ("Нелли", "Нелли"),  # indeclinable
        ("Анна-Мария", "Анна-Мария"),  # double names stay as is
        ("John", "John"),
        ("", ""),
    ],
)
def test_genitive_first_name(name: str, genitive: str) -> None:
    assert genitive_first_name(name) == genitive


def test_first_name() -> None:
    assert first_name("Анна Соколова") == "Анна"
    assert first_name("  Дмитрий  ") == "Дмитрий"
