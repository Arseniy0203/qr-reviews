"""Seed a demo beauty salon with ~300 reviews over the last 3 months (dev only).

    python -m scripts.seed           # does nothing if the demo salon already exists
    python -m scripts.seed --reset   # wipes ALL data, then seeds

What the data demonstrates:
- realistic time of day, including evening reviews 21:00-23:59 and night reviews after
  midnight Moscow time (00:00-02:59 MSK is the previous day in UTC, so day-grouping
  in the organization's timezone visibly differs from UTC grouping);
- ratings mostly 4-5; barber Dmitry has noticeably more negative reviews about «Запись»;
- QR point «Кресло у окна» is reassigned from Anna to Olga mid-period (assignment history);
- suspicious reviews: a burst from one device on Marina's card and copies of one long text
  on Olga's card, flagged with the same reason codes the antifraud service uses.
"""

import argparse
import asyncio
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.security import hash_identifier, hash_password
from app.models import (
    Base,
    Branch,
    ConsentDocument,
    ConsentType,
    Employee,
    Membership,
    Organization,
    Placement,
    QRAssignment,
    QRPoint,
    Review,
    ReviewCategory,
    Role,
    SuspiciousReason,
    User,
)
from app.schemas.organization import OrgSettings
from app.services.consent_service import get_current_document
from app.services.organization_service import create_organization
from app.services.qr_service import generate_unique_token

DEMO_ORG_NAME = "Салон красоты «Лаванда»"
DEMO_PASSWORD = "demo12345"
TOTAL_REVIEWS = 300
PERIOD_DAYS = 92
REASSIGN_AFTER_DAYS = 46
TZ = ZoneInfo("Europe/Moscow")

rng = random.Random(20261026)

# --------------------------------------------------------------------------- specs

RATINGS = (5, 4, 3, 2, 1)
NORMAL_RATING_WEIGHTS = (62, 23, 7, 4, 4)
PROBLEM_RATING_WEIGHTS = (24, 18, 14, 20, 24)


@dataclass(frozen=True)
class EmployeeSpec:
    key: str
    full_name: str
    position: str
    branch: str
    rating_weights: Sequence[int] = NORMAL_RATING_WEIGHTS
    booking_issues: bool = False

    @property
    def first_name(self) -> str:
        return self.full_name.split()[0]


@dataclass(frozen=True)
class PointSpec:
    key: str
    name: str
    branch: str
    placement: Placement
    weight: float
    employee: str | None = None
    reassign_to: str | None = None


BRANCHES = {
    "tver": ("Лаванда на Тверской", "Москва, ул. Тверская, 12"),
    "mira": ("Лаванда на Мира", "Москва, пр-т Мира, 45"),
}

EMPLOYEES = [
    EmployeeSpec("anna", "Анна Соколова", "Парикмахер-стилист", "tver"),
    EmployeeSpec("olga", "Ольга Миронова", "Колорист", "tver"),
    EmployeeSpec("ekaterina", "Екатерина Лебедева", "Мастер маникюра", "tver"),
    EmployeeSpec(
        "dmitry", "Дмитрий Орлов", "Барбер", "mira", PROBLEM_RATING_WEIGHTS, booking_issues=True
    ),
    EmployeeSpec("marina", "Марина Кузнецова", "Косметолог", "mira"),
    EmployeeSpec("svetlana", "Светлана Белова", "Мастер маникюра и педикюра", "mira"),
]

POINTS = [
    PointSpec("tver_desk", "Ресепшн на Тверской", "tver", Placement.COUNTER, 1.5),
    PointSpec("anna_card", "Визитка — Анна", "tver", Placement.BUSINESS_CARD, 1.0, "anna"),
    PointSpec("olga_card", "Визитка — Ольга", "tver", Placement.BUSINESS_CARD, 1.0, "olga"),
    PointSpec("tver_nails", "Стол маникюра №1", "tver", Placement.TABLE, 1.0, "ekaterina"),
    PointSpec("tver_window", "Кресло у окна", "tver", Placement.TABLE, 1.0, "anna", "olga"),
    PointSpec("mira_desk", "Ресепшн на Мира", "mira", Placement.COUNTER, 1.5),
    PointSpec("dmitry_card", "Визитка — Дмитрий", "mira", Placement.BUSINESS_CARD, 1.3, "dmitry"),
    PointSpec("marina_card", "Визитка — Марина", "mira", Placement.BUSINESS_CARD, 0.9, "marina"),
    PointSpec("mira_nails", "Стол маникюра на Мира", "mira", Placement.TABLE, 1.0, "svetlana"),
    PointSpec("telegram", "Telegram-канал салона", "mira", Placement.ONLINE, 0.6),
]

# Reviews are written after the visit: the evening tail (21-23) and a few night reviews
# after midnight (0-1, previous day in UTC) are intentional.
HOUR_WEIGHTS = {
    0: 2, 1: 1, 9: 2, 10: 4, 11: 6, 12: 7, 13: 7, 14: 8, 15: 8, 16: 8,
    17: 9, 18: 10, 19: 11, 20: 10, 21: 8, 22: 6, 23: 4,
}  # fmt: skip

SALON_WIFI_IPS = {"tver": "185.12.40.17", "mira": "94.25.170.8"}

# --------------------------------------------------------------------------- texts

POSITIVE = {
    "quality": [
        "Стрижка получилась именно такой, как хотелось.",
        "Маникюр держится уже третью неделю, ни одного скола.",
        "Окрашивание ровное, цвет точь-в-точь как на референсе.",
        "Очень аккуратная работа, всё до мелочей.",
        "Результат превзошёл ожидания.",
        "Брови идеально симметричные, форма подобрана под лицо.",
    ],
    "specialist": [
        "Мастер внимательно выслушал пожелания и подсказал по уходу.",
        "Чувствуется опыт: работа быстрая, но без спешки.",
        "Мастер — настоящий профессионал своего дела.",
        "Очень приятный в общении мастер, время пролетело незаметно.",
    ],
    "booking": [
        "Записалась онлайн за пару минут, приняли минута в минуту.",
        "Удобно, что напомнили о записи накануне.",
        "Нашли окошко в тот же день, спасибо администратору.",
    ],
    "hygiene": [
        "Инструменты достали из крафт-пакета при мне — это успокаивает.",
        "В салоне идеально чисто.",
    ],
    "atmosphere": [
        "Очень уютно, предложили кофе, играла приятная музыка.",
        "Атмосфера расслабляющая, хочется возвращаться.",
        "Красивый интерьер и вежливые администраторы.",
    ],
    "price": [
        "Цены адекватные для такого уровня.",
        "Стоимость назвали заранее, без сюрпризов.",
    ],
}
POSITIVE_GENERIC = [
    "Обязательно приду ещё!",
    "Рекомендую всем друзьям.",
    "Спасибо за отличный сервис!",
    "Всё понравилось.",
    "Теперь только к вам.",
]
NAME_PRAISE = ["{name} — золотые руки!", "Спасибо, {name}!", "{name}, вы лучшие!"]

NEUTRAL = {
    "quality": [
        "В целом нормально, но подстригли короче, чем просила.",
        "Покрытие хорошее, но форма ногтей не совсем та.",
    ],
    "booking": [
        "Пришлось подождать минут десять, но в целом всё хорошо.",
        "Записаться получилось только через неделю.",
    ],
    "price": ["Результат хороший, но дороговато."],
    "atmosphere": ["Было шумновато, хотя сама работа хорошая."],
    "specialist": ["Мастер неразговорчивый, но сделал аккуратно."],
}
NEUTRAL_GENERIC = ["Неплохо, но есть куда расти.", "Средне.", "Ожидала немного большего."]

NEGATIVE = {
    "quality": [
        "Покрытие начало отслаиваться через три дня.",
        "Стрижка неровная, пришлось исправлять в другом месте.",
        "Цвет получился совсем не тот, о котором договаривались.",
    ],
    "booking": [
        "Запись была на 15:00, а приняли только в 15:40 — никто не предупредил.",
        "Запись отменили за час до визита, причём уже второй раз.",
        "Пришёл к назначенному времени, а меня не оказалось в журнале записи.",
        "Онлайн-запись показывает, что окон нет, а по телефону не дозвониться.",
        "Перенесли запись в последний момент, пришлось отпрашиваться с работы дважды.",
    ],
    "price": ["Итоговая цена оказалась на треть выше той, что назвали при записи."],
    "specialist": ["Мастер всё время отвлекался на телефон."],
    "hygiene": ["Инструменты достали не из крафт-пакета, это насторожило."],
    "atmosphere": ["Администратор громко разговаривала по телефону на весь зал."],
}
NEGATIVE_GENERIC = ["Больше не приду.", "Очень разочарована.", "Не рекомендую.", "Обидно."]

POSITIVE_CATEGORY_WEIGHTS = {
    "quality": 5, "specialist": 5, "atmosphere": 3, "booking": 2, "hygiene": 2, "price": 2,
}  # fmt: skip
NEGATIVE_CATEGORY_WEIGHTS = {
    "quality": 5, "price": 3, "specialist": 3, "hygiene": 2, "atmosphere": 2, "booking": 1,
}  # fmt: skip

CUSTOMER_NAMES = ["Мария", "Алексей", "Ирина", "Наталья", "Сергей", "Юлия", "Ксения", "Павел"]

BURST_TEXTS = ["Супер!", "Лучший мастер!", "Всё отлично", None, "Рекомендую!", "5 звёзд"]
DUPLICATE_TEXT = (
    "Ольга — лучший колорист в Москве! Всем рекомендую этот салон, цены отличные, атмосфера супер."
)
DUPLICATE_VARIANTS = [
    DUPLICATE_TEXT,
    DUPLICATE_TEXT,
    DUPLICATE_TEXT.replace("!", "."),
    DUPLICATE_TEXT.lower(),
]

# --------------------------------------------------------------------------- helpers


def weighted_sample(weights: dict[str, int], k: int) -> list[str]:
    pool = dict(weights)
    chosen: list[str] = []
    while pool and len(chosen) < k:
        key = rng.choices(list(pool), list(pool.values()))[0]
        chosen.append(key)
        del pool[key]
    return chosen


def random_device() -> str:
    return hash_identifier(str(uuid.UUID(int=rng.getrandbits(128))))


def random_ip() -> str:
    return (
        f"{rng.randint(5, 223)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
    )


def random_phone() -> str:
    return "+79" + "".join(str(rng.randint(0, 9)) for _ in range(9))


class Timeline:
    """Draws review moments: growing volume over time, busier weekends, evening tail."""

    def __init__(self, now: datetime) -> None:
        self.now_local = now.astimezone(TZ)
        self.start_date = self.now_local.date() - timedelta(days=PERIOD_DAYS - 1)
        self.days = [self.start_date + timedelta(days=i) for i in range(PERIOD_DAYS)]
        self.day_weights = [
            (1 + 0.6 * i / PERIOD_DAYS) * (1.3 if day.weekday() >= 5 else 1.0)
            for i, day in enumerate(self.days)
        ]

    @property
    def start(self) -> datetime:
        return datetime.combine(self.start_date, time(0, 0), TZ).astimezone(UTC)

    def at(self, days_ago: int, hour: int, minute: int) -> datetime:
        day = self.now_local.date() - timedelta(days=days_ago)
        return datetime.combine(day, time(hour, minute), TZ).astimezone(UTC)

    def random_moment(self) -> datetime:
        while True:
            day = rng.choices(self.days, self.day_weights)[0]
            hour = rng.choices(list(HOUR_WEIGHTS), list(HOUR_WEIGHTS.values()))[0]
            moment = datetime.combine(day, time(hour, rng.randint(0, 59), rng.randint(0, 59)), TZ)
            if moment <= self.now_local:
                return moment.astimezone(UTC)


def compose_text(rating: int, categories: list[str], employee_name: str | None) -> str | None:
    if rating >= 4:
        bank, generic, no_text = POSITIVE, POSITIVE_GENERIC, 0.25
    elif rating == 3:
        bank, generic, no_text = NEUTRAL, NEUTRAL_GENERIC, 0.15
    else:
        bank, generic, no_text = NEGATIVE, NEGATIVE_GENERIC, 0.10
    if rng.random() < no_text:
        return None

    parts: list[str] = []
    for code in categories[:2]:
        if code in bank and (not parts or rng.random() < 0.5):
            parts.append(rng.choice(bank[code]))
    if not parts or rng.random() < 0.4:
        parts.append(rng.choice(generic))
    if employee_name and rating >= 4 and rng.random() < 0.35:
        parts.insert(0, rng.choice(NAME_PRAISE).format(name=employee_name))
    return " ".join(parts)


def pick_categories(rating: int, booking_issues: bool) -> list[str]:
    count = rng.choices([0, 1, 2, 3], [15, 40, 35, 10])[0]
    weights = POSITIVE_CATEGORY_WEIGHTS if rating >= 4 else NEGATIVE_CATEGORY_WEIGHTS
    chosen = weighted_sample(weights, count)
    if booking_issues and rating <= 3 and rng.random() < 0.95:
        chosen = ["booking", *[c for c in chosen if c != "booking"]][: max(count, 1)]
    return chosen


# --------------------------------------------------------------------------- seeding


@dataclass
class World:
    org: Organization
    branches: dict[str, Branch]
    employees: dict[str, Employee]
    points: dict[str, QRPoint]
    categories: dict[str, ReviewCategory]
    pd_document: ConsentDocument
    marketing_document: ConsentDocument
    # point key -> [(employee key, started_at, ended_at)]
    assignments: dict[str, list[tuple[str, datetime, datetime | None]]]

    def employee_at(self, point_key: str, moment: datetime) -> str | None:
        for employee_key, started, ended in self.assignments.get(point_key, []):
            if started <= moment and (ended is None or moment < ended):
                return employee_key
        return None


async def build_structure(session: AsyncSession, timeline: Timeline) -> World:
    org = await create_organization(
        session,
        name=DEMO_ORG_NAME,
        niche="beauty",
        settings=OrgSettings(
            collect_contact=True,
            yandex_maps_url="https://yandex.ru/maps/org/lavanda_demo/1000000001/",
            two_gis_url="https://2gis.ru/moscow/firm/70000001000000001",
            negative_threshold=3,
            timezone="Europe/Moscow",
            legal_name="ООО «Лаванда» (демо)",
            inn="7700000000",
            legal_address="125009, г. Москва, ул. Тверская, д. 12",
            privacy_email="privacy@lavanda.demo",
        ),
    )
    categories = {
        c.code: c
        for c in await session.scalars(
            select(ReviewCategory).where(ReviewCategory.organization_id == org.id)
        )
        if c.code
    }

    branches = {
        key: Branch(organization_id=org.id, name=name, address=address)
        for key, (name, address) in BRANCHES.items()
    }
    session.add_all(branches.values())
    await session.flush()

    employees = {
        spec.key: Employee(
            organization_id=org.id,
            branch_id=branches[spec.branch].id,
            full_name=spec.full_name,
            position=spec.position,
        )
        for spec in EMPLOYEES
    }
    session.add_all(employees.values())
    await session.flush()

    points: dict[str, QRPoint] = {}
    for spec in POINTS:
        points[spec.key] = QRPoint(
            organization_id=org.id,
            branch_id=branches[spec.branch].id,
            name=spec.name,
            placement=spec.placement,
            token=await generate_unique_token(session),
        )
        session.add(points[spec.key])
    await session.flush()

    # Cards were printed a month before the period starts.
    issued_at = timeline.start - timedelta(days=30)
    reassigned_at = timeline.start + timedelta(days=REASSIGN_AFTER_DAYS, hours=10)
    assignments: dict[str, list[tuple[str, datetime, datetime | None]]] = {}
    for spec in POINTS:
        if spec.employee is None:
            continue
        history: list[tuple[str, datetime, datetime | None]] = []
        if spec.reassign_to:
            history.append((spec.employee, issued_at, reassigned_at))
            history.append((spec.reassign_to, reassigned_at, None))
        else:
            history.append((spec.employee, issued_at, None))
        assignments[spec.key] = history
        session.add_all(
            QRAssignment(
                organization_id=org.id,
                qr_point_id=points[spec.key].id,
                employee_id=employees[employee_key].id,
                started_at=started,
                ended_at=ended,
            )
            for employee_key, started, ended in history
        )
    await session.flush()
    pd_document = await get_current_document(session, org.id, ConsentType.PD)
    marketing_document = await get_current_document(session, org.id, ConsentType.MARKETING)
    assert pd_document is not None
    assert marketing_document is not None
    return World(
        org,
        branches,
        employees,
        points,
        categories,
        pd_document,
        marketing_document,
        assignments,
    )


async def create_users(session: AsyncSession, world: World) -> list[tuple[str, str]]:
    password_hash = hash_password(DEMO_PASSWORD)
    accounts = [
        ("owner@lavanda.demo", "Елена Васильева", Role.OWNER, None, None),
        ("manager@lavanda.demo", "Игорь Петров", Role.MANAGER, "mira", None),
        ("anna@lavanda.demo", "Анна Соколова", Role.EMPLOYEE, None, "anna"),
    ]
    created = []
    for email, full_name, role, branch_key, employee_key in accounts:
        user = User(email=email, full_name=full_name, password_hash=password_hash)
        session.add(user)
        await session.flush()
        session.add(
            Membership(
                organization_id=world.org.id,
                user_id=user.id,
                role=role,
                branch_id=world.branches[branch_key].id if branch_key else None,
                employee_id=world.employees[employee_key].id if employee_key else None,
            )
        )
        created.append((email, role.value))
    await session.flush()
    return created


def make_review(
    world: World,
    point_key: str,
    created_at: datetime,
    rating: int,
    text_: str | None,
    category_codes: list[str],
    *,
    device_hash: str,
    ip: str,
    reasons: list[SuspiciousReason] | None = None,
    with_contact: bool = False,
) -> Review:
    point = world.points[point_key]
    employee_key = world.employee_at(point_key, created_at)
    review = Review(
        organization_id=world.org.id,
        branch_id=point.branch_id,
        qr_point_id=point.id,
        employee_id=world.employees[employee_key].id if employee_key else None,
        rating=rating,
        text=text_,
        is_suspicious=bool(reasons),
        suspicious_reasons=[r.value for r in reasons or []],
        device_hash=device_hash,
        ip_hash=hash_identifier(ip),
        created_at=created_at,
    )
    review.categories = [world.categories[code] for code in category_codes]
    if with_contact:
        consent_at = created_at + timedelta(minutes=1)
        review.customer_name = rng.choice(CUSTOMER_NAMES)
        review.customer_phone = random_phone()
        review.pd_consent_document_id = world.pd_document.id
        review.pd_consent_at = consent_at
        # Marketing consent is a separate, optional checkbox: about half agree.
        if rng.random() < 0.5:
            review.marketing_consent_document_id = world.marketing_document.id
            review.marketing_consent_at = consent_at
    return review


def revoke_one_consent(reviews: list[Review], now: datetime) -> None:
    """Demonstrate a 152-FZ revocation: contact wiped, consent evidence and date kept."""
    for review in sorted(reviews, key=lambda r: r.created_at):
        if review.customer_phone and review.created_at < now - timedelta(days=30):
            revoked_at = review.created_at + timedelta(days=10)
            review.customer_name = None
            review.customer_phone = None
            review.pd_consent_revoked_at = revoked_at
            if review.marketing_consent_document_id:
                review.marketing_consent_revoked_at = revoked_at
            return


def regular_reviews(world: World, timeline: Timeline, count: int) -> list[Review]:
    specs = {spec.key: spec for spec in EMPLOYEES}
    point_specs = {spec.key: spec for spec in POINTS}
    reviews = []
    for _ in range(count):
        point_key = rng.choices(list(point_specs), [p.weight for p in POINTS])[0]
        created_at = timeline.random_moment()
        employee_key = world.employee_at(point_key, created_at)
        employee = specs[employee_key] if employee_key else None
        weights = employee.rating_weights if employee else NORMAL_RATING_WEIGHTS
        rating = rng.choices(RATINGS, weights)[0]
        categories = pick_categories(rating, employee.booking_issues if employee else False)
        branch = point_specs[point_key].branch
        reviews.append(
            make_review(
                world,
                point_key,
                created_at,
                rating,
                compose_text(rating, categories, employee.first_name if employee else None),
                categories,
                device_hash=random_device(),
                ip=SALON_WIFI_IPS[branch] if rng.random() < 0.3 else random_ip(),
                with_contact=rng.random() < 0.12,
            )
        )
    return reviews


def suspicious_reviews(world: World, timeline: Timeline) -> list[Review]:
    settings = get_settings()
    reviews = []

    # 1) Burst: one device leaves 12 five-star reviews on Marina's card within ~14 minutes.
    device, ip = random_device(), random_ip()
    start = timeline.at(days_ago=19, hour=20, minute=5)
    for i in range(12):
        reasons: list[SuspiciousReason] = []
        if i + 1 > settings.af_device_max_per_point:
            reasons.append(SuspiciousReason.DEVICE_REPEAT)
        burst = i + 1 > settings.af_burst_point_max
        if burst:
            reasons.append(SuspiciousReason.POINT_BURST)
        # IP is a weak signal: soft threshold counts only together with a burst.
        if i + 1 >= settings.af_ip_hard_threshold or (
            i + 1 >= settings.af_ip_soft_threshold and burst
        ):
            reasons.append(SuspiciousReason.IP_REPEAT)
        reviews.append(
            make_review(
                world,
                "marina_card",
                start + timedelta(seconds=70 * i),
                5,
                BURST_TEXTS[i % len(BURST_TEXTS)],
                ["specialist"] if i % 2 else [],
                device_hash=device,
                ip=ip,
                reasons=reasons,
            )
        )

    # 2) The same long text from different devices on Olga's card over three days.
    for i, (days_ago, variant) in enumerate(zip([9, 8, 8, 7], DUPLICATE_VARIANTS, strict=True)):
        reviews.append(
            make_review(
                world,
                "olga_card",
                timeline.at(days_ago=days_ago, hour=21 + i % 3, minute=10 + 7 * i),
                5,
                variant,
                ["quality", "price", "atmosphere"],
                device_hash=random_device(),
                ip=random_ip(),
                reasons=[SuspiciousReason.DUPLICATE_TEXT] if i else [],
            )
        )
    return reviews


async def reset_database(session: AsyncSession) -> None:
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


async def print_summary(session: AsyncSession, world: World, users: list[tuple[str, str]]) -> None:
    settings = get_settings()
    org_id = world.org.id
    org_tz = OrgSettings.from_db(world.org.settings).timezone
    local_hour = func.extract("hour", func.timezone(org_tz, Review.created_at))
    total, suspicious, evening, average = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(Review.is_suspicious),
                func.count().filter(local_hour >= 21),
                func.round(func.avg(Review.rating), 2),
            ).where(Review.organization_id == org_id)
        )
    ).one()
    print(f"\n✔ {DEMO_ORG_NAME}: {total} отзывов, средняя {average}")
    print(f"  подозрительных: {suspicious}, вечерних (21:00–23:59 МСК): {evening}")
    contacts, marketing, revoked = (
        await session.execute(
            select(
                func.count(Review.customer_phone),
                func.count(Review.marketing_consent_document_id).filter(
                    Review.marketing_consent_revoked_at.is_(None)
                ),
                func.count(Review.pd_consent_revoked_at),
            ).where(Review.organization_id == org_id)
        )
    ).one()
    print(f"  с контактом: {contacts}, согласие на рекламу: {marketing}, отозвано: {revoked}")

    rows = await session.execute(
        select(Employee.full_name, func.count(Review.id), func.round(func.avg(Review.rating), 2))
        .join(Review, Review.employee_id == Employee.id)
        .where(Employee.organization_id == org_id)
        .group_by(Employee.full_name)
        .order_by(Employee.full_name)
    )
    for name, count, avg in rows:
        print(f"  {name:<20} {count:>3} отзывов, средняя {avg}")

    print(f"\nВход в кабинет (пароль для всех: {DEMO_PASSWORD}):")
    for email, role in users:
        print(f"  {email:<24} {role}")
    print("\nQR-точки:")
    for point in world.points.values():
        print(f"  {point.name:<24} {settings.public_base_url}/r/{point.token}")


async def main(reset: bool) -> None:
    settings = get_settings()
    if settings.is_prod:
        raise SystemExit("Seed is for dev/demo environments only (APP_ENV=prod).")

    async with get_sessionmaker()() as session:
        if reset:
            await reset_database(session)
            await session.commit()
        elif await session.scalar(
            select(Organization.id).where(Organization.name == DEMO_ORG_NAME)
        ):
            print("Демо-данные уже загружены. Для пересоздания: python -m scripts.seed --reset")
            return

        now = datetime.now(UTC)
        timeline = Timeline(now)
        world = await build_structure(session, timeline)
        users = await create_users(session, world)
        special = suspicious_reviews(world, timeline)
        regular = regular_reviews(world, timeline, TOTAL_REVIEWS - len(special))
        revoke_one_consent(regular, now)
        session.add_all([*special, *regular])
        await session.commit()
        await print_summary(session, world, users)
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="wipe all data before seeding")
    asyncio.run(main(parser.parse_args().reset))
