"""Employees. Owners manage all; managers only their branch; employees see themselves."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Employee, Role
from app.schemas.employee import EmployeeCreate, EmployeeUpdate
from app.services.assignments import close_open_assignments
from app.services.branch_service import get_active_branch
from app.services.scope import AccessScope


def _visible(scope: AccessScope, employee: Employee) -> bool:
    if scope.role is Role.OWNER:
        return True
    if scope.role is Role.MANAGER:
        return employee.branch_id == scope.branch_id
    return employee.id == scope.employee_id


async def list_employees(
    session: AsyncSession,
    scope: AccessScope,
    *,
    branch_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> list[Employee]:
    query = select(Employee).where(scope.org_filter(Employee))
    if scope.role is Role.MANAGER:
        query = query.where(Employee.branch_id == scope.branch_id)
    elif scope.role is Role.EMPLOYEE:
        query = query.where(Employee.id == scope.employee_id)
    if branch_id is not None:
        query = query.where(Employee.branch_id == branch_id)
    if not include_archived:
        query = query.where(Employee.archived_at.is_(None))
    return list(await session.scalars(query.order_by(Employee.full_name)))


async def get_employee(
    session: AsyncSession, scope: AccessScope, employee_id: uuid.UUID
) -> Employee:
    employee = await session.get(Employee, employee_id)
    scope.ensure_same_org(employee)
    assert employee is not None
    if not _visible(scope, employee):
        raise NotFoundError()
    return employee


async def create_employee(
    session: AsyncSession, scope: AccessScope, data: EmployeeCreate
) -> Employee:
    scope.require_role(Role.OWNER, Role.MANAGER)
    branch = await get_active_branch(session, scope, data.branch_id)
    employee = Employee(
        organization_id=scope.organization_id,
        branch_id=branch.id,
        full_name=data.full_name,
        position=data.position,
    )
    session.add(employee)
    await session.commit()
    return employee


async def update_employee(
    session: AsyncSession, scope: AccessScope, employee_id: uuid.UUID, data: EmployeeUpdate
) -> Employee:
    scope.require_role(Role.OWNER, Role.MANAGER)
    employee = await get_employee(session, scope, employee_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("branch_id") is not None and changes["branch_id"] != employee.branch_id:
        # Managers cannot see other branches, so they cannot move employees out.
        await get_active_branch(session, scope, changes["branch_id"])
    for field, value in changes.items():
        if value is None and field in {"branch_id", "full_name", "is_active"}:
            continue
        setattr(employee, field, value)
    await session.commit()
    return employee


async def archive_employee(
    session: AsyncSession, scope: AccessScope, employee_id: uuid.UUID
) -> Employee:
    """Soft delete: open QR assignments are closed; reviews keep pointing at the employee."""
    scope.require_role(Role.OWNER, Role.MANAGER)
    employee = await get_employee(session, scope, employee_id)
    if employee.archived_at is None:
        now = datetime.now(UTC)
        employee.archived_at = now
        await close_open_assignments(session, scope.organization_id, now, employee_id=employee.id)
        await session.commit()
    return employee


async def restore_employee(
    session: AsyncSession, scope: AccessScope, employee_id: uuid.UUID
) -> Employee:
    scope.require_role(Role.OWNER, Role.MANAGER)
    employee = await get_employee(session, scope, employee_id)
    employee.archived_at = None
    await session.commit()
    return employee
