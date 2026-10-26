"""Genitive case of Russian first names: «мастера Анны», «мастера Дмитрия».

Rule-based on the ending with a small exception list. When a name does not match
a known pattern it is returned unchanged: an undeclined name reads better than a
wrongly declined one.
"""

import re

_EXCEPTIONS = {
    # fleeting vowels
    "павел": "павла",
    "лев": "льва",
    "пётр": "петра",
    "петр": "петра",
    # female names ending in a soft sign
    "любовь": "любови",
    "нинель": "нинель",
    "айгуль": "айгуль",
    "гузель": "гузель",
    "адель": "адель",
}
# Single Cyrillic word only; double names («Анна-Мария») stay undeclined.
_CYRILLIC_NAME = re.compile(r"^[А-Яа-яЁё]+$")
# After these letters Russian writes «и», not «ы»: Ольга -> Ольги.
_VELAR_OR_SIBILANT = set("гкхжчшщ")
_CONSONANTS = set("бвгджзклмнпрстфхцчшщ")


def _match_case(source: str, target: str) -> str:
    return target.capitalize() if source[:1].isupper() else target


def genitive_first_name(name: str) -> str:
    name = name.strip()
    if not _CYRILLIC_NAME.match(name):
        return name
    lower = name.lower()
    if lower in _EXCEPTIONS:
        return _match_case(name, _EXCEPTIONS[lower])

    if lower.endswith("ия") or lower.endswith("я"):  # Мария -> Марии
        result = lower[:-1] + "и"
    elif lower.endswith("а"):  # Анна -> Анны, Ольга -> Ольги, Никита -> Никиты
        result = lower[:-1] + ("и" if lower[-2:-1] in _VELAR_OR_SIBILANT else "ы")
    elif lower.endswith(("й", "ь")):  # Дмитрий -> Дмитрия, Игорь -> Игоря
        result = lower[:-1] + "я"
    elif lower[-1] in _CONSONANTS:  # Иван -> Ивана
        result = lower + "а"
    else:  # -о, -е, -и, -у, -ю, -э, -ы: indeclinable (Марко, Нелли)
        return name
    return _match_case(name, result)


def first_name(full_name: str) -> str:
    return full_name.strip().split()[0] if full_name.strip() else full_name
