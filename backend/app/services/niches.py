"""Niche templates: everything niche-specific lives in app/niches/*.yaml, not in code."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

NICHES_DIR = Path(__file__).resolve().parent.parent / "niches"


class NicheCategory(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=50)
    name: str = Field(min_length=1, max_length=100)


class NicheFormTexts(BaseModel):
    title: str
    title_with_employee: str = Field(pattern=r"\{name\}")
    rating_prompt: str
    categories_prompt: str
    text_label: str
    text_placeholder: str
    submit: str
    thanks_title: str
    thanks_text: str


class NicheTemplate(BaseModel):
    code: str
    title: str
    employee_title: str
    employee_title_plural: str
    categories: list[NicheCategory] = Field(min_length=1)
    form: NicheFormTexts
    contact_offer_default: str

    @field_validator("categories")
    @classmethod
    def _unique_codes(cls, value: list[NicheCategory]) -> list[NicheCategory]:
        codes = [c.code for c in value]
        if len(codes) != len(set(codes)):
            raise ValueError("Category codes must be unique within a niche")
        return value


class UnknownNicheError(ValueError):
    pass


@lru_cache
def load_niches() -> dict[str, NicheTemplate]:
    niches: dict[str, NicheTemplate] = {}
    for path in sorted(NICHES_DIR.glob("*.yaml")):
        template = NicheTemplate.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if template.code != path.stem:
            raise ValueError(f"{path.name}: code '{template.code}' must match the file name")
        niches[template.code] = template
    return niches


def get_niche(code: str) -> NicheTemplate:
    try:
        return load_niches()[code]
    except KeyError:
        raise UnknownNicheError(code) from None
