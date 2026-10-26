"""Closing open QR assignments; shared by QR, employee and branch services."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QRAssignment


async def close_open_assignments(
    session: AsyncSession,
    organization_id: uuid.UUID,
    at: datetime,
    *,
    point_ids: Sequence[uuid.UUID] | None = None,
    employee_id: uuid.UUID | None = None,
) -> None:
    """End open assignments of the given points or employee. History rows are kept."""
    query = update(QRAssignment).where(
        QRAssignment.organization_id == organization_id,
        QRAssignment.ended_at.is_(None),
    )
    if point_ids is not None:
        query = query.where(QRAssignment.qr_point_id.in_(point_ids))
    if employee_id is not None:
        query = query.where(QRAssignment.employee_id == employee_id)
    await session.execute(query.values(ended_at=at))
