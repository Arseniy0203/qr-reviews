"""Antifraud signals for public reviews. Reviews are never blocked, only flagged.

Rules (thresholds in env, AF_*):
- device_repeat: more than AF_DEVICE_MAX_PER_POINT reviews from one device on one point
  within AF_DEVICE_WINDOW_HOURS;
- point_burst / employee_burst: more than AF_BURST_*_MAX reviews within
  AF_BURST_WINDOW_MINUTES (sliding window);
- duplicate_text: simhash of a normalized text (only texts >= AF_SIMHASH_MIN_LENGTH chars)
  within AF_SIMHASH_MAX_DISTANCE bits of a recent text of the same organization;
- ip_repeat: IP is a weak signal because of shared salon Wi-Fi. AF_IP_SOFT_THRESHOLD
  counts only together with a burst or a duplicate text; AF_IP_HARD_THRESHOLD flags alone.

Redis unavailable -> no flags (fail-open), the review is still saved.
"""

import hashlib
import itertools
import logging
import re
import time
import uuid
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.redis import get_redis
from app.models import SuspiciousReason

logger = logging.getLogger(__name__)

_NON_WORD = re.compile(r"[^0-9a-zа-я]+")


@dataclass(frozen=True)
class FraudContext:
    organization_id: uuid.UUID
    qr_point_id: uuid.UUID
    employee_id: uuid.UUID | None
    device_hash: str | None
    ip_hash: str
    text: str | None


# --------------------------------------------------------------------------- simhash


def normalize_text(text: str) -> str:
    """Lowercase, ё->е, keep only letters and digits separated by single spaces."""
    return _NON_WORD.sub(" ", text.lower().replace("ё", "е")).strip()


def _hash64(feature: str) -> int:
    return int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")


def simhash(normalized: str) -> int:
    words = normalized.split()
    features = words + [f"{a} {b}" for a, b in itertools.pairwise(words)]
    weights = [0] * 64
    for feature in features:
        value = _hash64(feature)
        for bit in range(64):
            weights[bit] += 1 if value >> bit & 1 else -1
    return sum(1 << bit for bit in range(64) if weights[bit] > 0)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


# --------------------------------------------------------------------------- rules


async def _count_in_window(key: str, window_seconds: int) -> int:
    redis = get_redis()
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = await pipe.execute()
    return int(count)


async def _sliding_count(key: str, window_seconds: int) -> int:
    redis = get_redis()
    now = time.time()
    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.zadd(key, {member: now})
        pipe.zremrangebyscore(key, 0, now - window_seconds)
        pipe.zcard(key)
        pipe.expire(key, window_seconds)
        _, _, count, _ = await pipe.execute()
    return int(count)


async def _is_duplicate_text(organization_id: uuid.UUID, text: str | None) -> bool:
    settings = get_settings()
    if not text:
        return False
    normalized = normalize_text(text)
    if len(normalized) < settings.af_simhash_min_length:
        return False  # short texts like «всё супер» are naturally similar

    fingerprint = simhash(normalized)
    key = f"af:sim:{organization_id}"
    redis = get_redis()
    recent = await redis.lrange(key, 0, settings.af_simhash_history_size - 1)
    duplicate = any(
        hamming(fingerprint, int(previous)) <= settings.af_simhash_max_distance
        for previous in recent
    )
    async with redis.pipeline(transaction=True) as pipe:
        pipe.lpush(key, str(fingerprint))
        pipe.ltrim(key, 0, settings.af_simhash_history_size - 1)
        pipe.expire(key, settings.af_simhash_ttl_days * 86400)
        await pipe.execute()
    return duplicate


async def evaluate(ctx: FraudContext) -> list[SuspiciousReason]:
    settings = get_settings()
    reasons: list[SuspiciousReason] = []
    try:
        if ctx.device_hash:
            device_count = await _count_in_window(
                f"af:dev:{ctx.qr_point_id}:{ctx.device_hash}",
                settings.af_device_window_hours * 3600,
            )
            if device_count > settings.af_device_max_per_point:
                reasons.append(SuspiciousReason.DEVICE_REPEAT)

        burst_window = settings.af_burst_window_minutes * 60
        point_burst = (
            await _sliding_count(f"af:burst:point:{ctx.qr_point_id}", burst_window)
            > settings.af_burst_point_max
        )
        if point_burst:
            reasons.append(SuspiciousReason.POINT_BURST)
        employee_burst = False
        if ctx.employee_id:
            employee_burst = (
                await _sliding_count(f"af:burst:employee:{ctx.employee_id}", burst_window)
                > settings.af_burst_employee_max
            )
            if employee_burst:
                reasons.append(SuspiciousReason.EMPLOYEE_BURST)

        duplicate = await _is_duplicate_text(ctx.organization_id, ctx.text)
        if duplicate:
            reasons.append(SuspiciousReason.DUPLICATE_TEXT)

        ip_count = await _count_in_window(
            f"af:ip:{ctx.qr_point_id}:{ctx.ip_hash}", settings.af_ip_window_hours * 3600
        )
        strong_signal = point_burst or employee_burst or duplicate
        if ip_count >= settings.af_ip_hard_threshold or (
            ip_count >= settings.af_ip_soft_threshold and strong_signal
        ):
            reasons.append(SuspiciousReason.IP_REPEAT)
    except RedisError:
        logger.warning("Antifraud unavailable (Redis error): review saved without checks")
        return []
    return reasons
