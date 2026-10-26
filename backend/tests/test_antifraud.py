"""Antifraud rules: flag, never block; IP is a weak signal; short texts are not compared."""

import uuid
from collections.abc import Callable

import httpx
import pytest
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Review, SuspiciousReason
from app.services import antifraud
from app.services.antifraud import FraudContext, evaluate, hamming, normalize_text, simhash
from tests.factories import Team
from tests.public_helpers import client_from, open_form, post_review

ORG = uuid.uuid4()
LONG_TEXT = "Ольга лучший колорист в Москве, всем рекомендую этот салон, цены отличные!"


def ctx(**overrides: object) -> FraudContext:
    fields: dict[str, object] = {
        "organization_id": ORG,
        "qr_point_id": uuid.uuid4(),
        "employee_id": None,
        "device_hash": uuid.uuid4().hex,
        "ip_hash": uuid.uuid4().hex,
        "text": None,
    }
    fields.update(overrides)
    return FraudContext(**fields)  # type: ignore[arg-type]


@pytest.fixture
def af(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Set antifraud thresholds for a test."""

    def configure(**values: int) -> None:
        for name, value in values.items():
            monkeypatch.setattr(get_settings(), f"af_{name}", value)

    return configure


# --- unit: text fingerprints -------------------------------------------------------


def test_normalize_text() -> None:
    assert normalize_text("Всё СУПЕР!!!  Ёлки-палки") == "все супер елки палки"


def test_simhash_close_for_near_duplicates_far_for_different() -> None:
    a = simhash(normalize_text(LONG_TEXT))
    b = simhash(normalize_text(LONG_TEXT.upper().replace(",", ";")))
    c = simhash(normalize_text("Записали на три часа, приняли только в четыре, больше не приду."))
    assert hamming(a, b) == 0
    assert hamming(a, c) > get_settings().af_simhash_max_distance


# --- rules -------------------------------------------------------------------------


async def test_device_repeat_only_on_same_point() -> None:
    point, device = uuid.uuid4(), uuid.uuid4().hex
    assert await evaluate(ctx(qr_point_id=point, device_hash=device)) == []
    assert await evaluate(ctx(qr_point_id=uuid.uuid4(), device_hash=device)) == []
    assert SuspiciousReason.DEVICE_REPEAT in await evaluate(
        ctx(qr_point_id=point, device_hash=device)
    )


async def test_ip_alone_below_hard_threshold_is_not_flagged(af: Callable[..., None]) -> None:
    af(ip_soft_threshold=2, ip_hard_threshold=20)
    point, ip = uuid.uuid4(), uuid.uuid4().hex
    for _ in range(5):  # shared salon Wi-Fi: many honest customers, one IP
        assert await evaluate(ctx(qr_point_id=point, ip_hash=ip)) == []


async def test_ip_soft_threshold_counts_with_duplicate_text(af: Callable[..., None]) -> None:
    af(ip_soft_threshold=2, ip_hard_threshold=20)
    point, ip = uuid.uuid4(), uuid.uuid4().hex
    assert await evaluate(ctx(qr_point_id=point, ip_hash=ip, text=LONG_TEXT)) == []
    reasons = await evaluate(ctx(qr_point_id=point, ip_hash=ip, text=LONG_TEXT))
    assert reasons == [SuspiciousReason.DUPLICATE_TEXT, SuspiciousReason.IP_REPEAT]


async def test_ip_hard_threshold_flags_alone(af: Callable[..., None]) -> None:
    af(ip_soft_threshold=2, ip_hard_threshold=3)
    point, ip = uuid.uuid4(), uuid.uuid4().hex
    results = [await evaluate(ctx(qr_point_id=point, ip_hash=ip)) for _ in range(3)]
    assert results == [[], [], [SuspiciousReason.IP_REPEAT]]


async def test_point_and_employee_bursts(af: Callable[..., None]) -> None:
    af(burst_point_max=2, burst_employee_max=3)
    point, employee = uuid.uuid4(), uuid.uuid4()
    results = [await evaluate(ctx(qr_point_id=point, employee_id=employee)) for _ in range(4)]
    assert results[:2] == [[], []]
    assert results[2] == [SuspiciousReason.POINT_BURST]
    assert results[3] == [SuspiciousReason.POINT_BURST, SuspiciousReason.EMPLOYEE_BURST]


async def test_short_texts_are_never_duplicates() -> None:
    for _ in range(3):
        assert await evaluate(ctx(text="Всё супер, спасибо!")) == []


async def test_near_duplicate_long_texts_flagged_within_org_only() -> None:
    assert await evaluate(ctx(text=LONG_TEXT)) == []
    assert await evaluate(ctx(text=LONG_TEXT.lower() + " ")) == [SuspiciousReason.DUPLICATE_TEXT]
    assert await evaluate(ctx(organization_id=uuid.uuid4(), text=LONG_TEXT)) == []


async def test_redis_failure_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*_: object) -> int:
        raise RedisError("down")

    monkeypatch.setattr(antifraud, "_count_in_window", broken)
    assert await evaluate(ctx()) == []


# --- end to end: flagged reviews are saved -------------------------------------------


async def test_repeat_from_same_device_is_saved_and_flagged(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    token = team.tenant.point.token
    form_token, device = await open_form(client, token)
    first = await post_review(client, token, form_token=form_token, device=device)
    second = await post_review(client_from("10.1.1.1"), token, form_token=form_token, device=device)
    assert first.status_code == second.status_code == 303  # never blocked

    reviews = (await session.scalars(select(Review).order_by(Review.id))).all()
    assert len(reviews) == 2
    assert reviews[0].is_suspicious is False
    assert reviews[1].is_suspicious is True
    assert reviews[1].suspicious_reasons == ["device_repeat"]
