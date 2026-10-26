"""QR points: tokens, CRUD, employee (re)assignment, file rendering."""

import asyncio
import secrets
import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Select, and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.models import Branch, Employee, QRAssignment, QRPoint, Role
from app.schemas.qr_point import (
    AssignmentOut,
    EmployeeRef,
    PrintLayout,
    QRPointCreate,
    QRPointOut,
    QRPointUpdate,
)
from app.services import qr_render
from app.services.assignments import close_open_assignments
from app.services.branch_service import get_active_branch
from app.services.employee_service import get_employee
from app.services.scope import AccessScope

# Lowercase, no look-alike characters (0/o, 1/l/i): easy to read off a printed card.
TOKEN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
TOKEN_LENGTH = 10

FileFormat = Literal["png", "svg", "pdf"]
MEDIA_TYPES: dict[str, str] = {
    "png": "image/png",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
}

POINT_ARCHIVED = "QR-точка в архиве"
EMPLOYEE_ARCHIVED = "Сотрудник в архиве"


def generate_token() -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


async def generate_unique_token(session: AsyncSession, attempts: int = 5) -> str:
    for _ in range(attempts):
        token = generate_token()
        taken = await session.scalar(select(exists().where(QRPoint.token == token)))
        if not taken:
            return token
    raise RuntimeError("Could not generate a unique QR token")


def public_url(token: str) -> str:
    return f"{get_settings().public_base_url}/r/{token}"


def short_url(token: str) -> str:
    """The link printed under the code: no scheme, so it is easy to type."""
    return public_url(token).split("://", 1)[-1]


# --------------------------------------------------------------------------- reading


def _points_query(scope: AccessScope) -> Select:
    return (
        select(QRPoint, Branch.name, Employee.id, Employee.full_name)
        .join(Branch, Branch.id == QRPoint.branch_id)
        .outerjoin(
            QRAssignment,
            and_(QRAssignment.qr_point_id == QRPoint.id, QRAssignment.ended_at.is_(None)),
        )
        .outerjoin(Employee, Employee.id == QRAssignment.employee_id)
        .where(scope.org_filter(QRPoint), scope.branch_filter(QRPoint.branch_id))
    )


def _to_out(row: tuple[QRPoint, str, uuid.UUID | None, str | None]) -> QRPointOut:
    point, branch_name, employee_id, employee_name = row
    return QRPointOut(
        id=point.id,
        branch_id=point.branch_id,
        branch_name=branch_name,
        name=point.name,
        placement=point.placement,
        token=point.token,
        url=public_url(point.token),
        is_active=point.is_active,
        archived_at=point.archived_at,
        created_at=point.created_at,
        current_employee=(
            EmployeeRef(id=employee_id, full_name=employee_name)
            if employee_id and employee_name
            else None
        ),
    )


async def list_points(
    session: AsyncSession,
    scope: AccessScope,
    *,
    branch_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> list[QRPointOut]:
    scope.require_role(Role.OWNER, Role.MANAGER)
    query = _points_query(scope)
    if branch_id is not None:
        query = query.where(QRPoint.branch_id == branch_id)
    if not include_archived:
        query = query.where(QRPoint.archived_at.is_(None))
    rows = await session.execute(query.order_by(Branch.name, QRPoint.name))
    return [_to_out(tuple(row)) for row in rows]


async def get_point_out(
    session: AsyncSession, scope: AccessScope, point_id: uuid.UUID
) -> QRPointOut:
    scope.require_role(Role.OWNER, Role.MANAGER)
    row = (await session.execute(_points_query(scope).where(QRPoint.id == point_id))).first()
    if row is None:
        raise NotFoundError()
    return _to_out(tuple(row))


async def _get_point(
    session: AsyncSession, scope: AccessScope, point_id: uuid.UUID, *, lock: bool = False
) -> QRPoint:
    scope.require_role(Role.OWNER, Role.MANAGER)
    query = select(QRPoint).where(QRPoint.id == point_id)
    if lock:
        query = query.with_for_update()
    point = await session.scalar(query)
    scope.ensure_branch(point)
    assert point is not None
    return point


# --------------------------------------------------------------------------- writing


async def create_point(
    session: AsyncSession, scope: AccessScope, data: QRPointCreate
) -> QRPointOut:
    scope.require_role(Role.OWNER, Role.MANAGER)
    branch = await get_active_branch(session, scope, data.branch_id)
    point = QRPoint(
        organization_id=scope.organization_id,
        branch_id=branch.id,
        name=data.name,
        placement=data.placement,
        token=await generate_unique_token(session),
    )
    session.add(point)
    await session.flush()
    if data.employee_id is not None:
        await _assign(session, scope, point, data.employee_id)
    await session.commit()
    return await get_point_out(session, scope, point.id)


async def update_point(
    session: AsyncSession, scope: AccessScope, point_id: uuid.UUID, data: QRPointUpdate
) -> QRPointOut:
    point = await _get_point(session, scope, point_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(point, field, value)
    await session.commit()
    return await get_point_out(session, scope, point_id)


async def set_archived(
    session: AsyncSession, scope: AccessScope, point_id: uuid.UUID, archived: bool
) -> QRPointOut:
    """Archived points show a stub page; their assignment is closed, history is kept."""
    point = await _get_point(session, scope, point_id, lock=True)
    if archived and point.archived_at is None:
        now = datetime.now(UTC)
        point.archived_at = now
        await close_open_assignments(session, scope.organization_id, now, point_ids=[point.id])
    elif not archived:
        branch = await session.get(Branch, point.branch_id)
        if branch is not None and branch.archived_at is not None:
            raise AppError("Сначала восстановите филиал")
        point.archived_at = None
    await session.commit()
    return await get_point_out(session, scope, point_id)


async def _assign(
    session: AsyncSession, scope: AccessScope, point: QRPoint, employee_id: uuid.UUID | None
) -> None:
    if point.archived_at is not None:
        raise AppError(POINT_ARCHIVED)
    current = await session.scalar(
        select(QRAssignment).where(
            QRAssignment.qr_point_id == point.id, QRAssignment.ended_at.is_(None)
        )
    )
    if employee_id is not None:
        # Managers only see employees of their branch; owners may assign anyone in the org,
        # including a master from another branch (a deliberate product decision).
        employee = await get_employee(session, scope, employee_id)
        if employee.archived_at is not None:
            raise AppError(EMPLOYEE_ARCHIVED)
    if current is not None and current.employee_id == employee_id:
        return

    now = datetime.now(UTC)
    if current is not None:
        current.ended_at = now
        await session.flush()  # free the "one open assignment" slot before inserting
    if employee_id is not None:
        session.add(
            QRAssignment(
                organization_id=scope.organization_id,
                qr_point_id=point.id,
                employee_id=employee_id,
                started_at=now,
                assigned_by_user_id=scope.user_id,
            )
        )
        await session.flush()


async def assign_employee(
    session: AsyncSession, scope: AccessScope, point_id: uuid.UUID, employee_id: uuid.UUID | None
) -> QRPointOut:
    """Reassignment closes the open history row and opens a new one (row lock on the point)."""
    point = await _get_point(session, scope, point_id, lock=True)
    await _assign(session, scope, point, employee_id)
    await session.commit()
    return await get_point_out(session, scope, point_id)


async def list_assignments(
    session: AsyncSession, scope: AccessScope, point_id: uuid.UUID
) -> list[AssignmentOut]:
    await _get_point(session, scope, point_id)
    rows = await session.execute(
        select(QRAssignment, Employee.full_name)
        .join(Employee, Employee.id == QRAssignment.employee_id)
        .where(QRAssignment.qr_point_id == point_id)
        .order_by(QRAssignment.started_at.desc(), QRAssignment.id.desc())
    )
    return [
        AssignmentOut(
            id=assignment.id,
            employee=EmployeeRef(id=assignment.employee_id, full_name=name),
            started_at=assignment.started_at,
            ended_at=assignment.ended_at,
        )
        for assignment, name in rows
    ]


# --------------------------------------------------------------------------- files


def _label(point: QRPointOut) -> qr_render.QRLabel:
    return qr_render.QRLabel(
        url=point.url,
        short_url=short_url(point.token),
        caption=f"{point.branch_name} · {point.name}",
    )


async def render_point_file(
    session: AsyncSession,
    scope: AccessScope,
    point_id: uuid.UUID,
    file_format: FileFormat,
    with_caption: bool,
) -> tuple[bytes, str]:
    point = await get_point_out(session, scope, point_id)
    label = _label(point)
    if file_format == "png":
        content = await asyncio.to_thread(qr_render.render_png, label, with_caption)
    elif file_format == "svg":
        content = await asyncio.to_thread(qr_render.render_svg, label, with_caption)
    else:
        content = await asyncio.to_thread(qr_render.render_pdf, label)
    return content, f"qr-{point.token}.{file_format}"


async def render_print_sheet(
    session: AsyncSession, scope: AccessScope, point_ids: list[uuid.UUID], layout: PrintLayout
) -> bytes:
    """All requested points must be visible to the caller and not archived."""
    scope.require_role(Role.OWNER, Role.MANAGER)
    unique_ids = list(dict.fromkeys(point_ids))
    rows = await session.execute(
        _points_query(scope).where(QRPoint.id.in_(unique_ids), QRPoint.archived_at.is_(None))
    )
    by_id = {out.id: out for out in (_to_out(tuple(row)) for row in rows)}
    if len(by_id) != len(unique_ids):
        raise NotFoundError("Некоторые QR-точки не найдены или в архиве")
    labels = [_label(by_id[point_id]) for point_id in unique_ids]
    return await asyncio.to_thread(qr_render.render_sheet, labels, layout)
