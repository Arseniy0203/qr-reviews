"""Helpers that build minimal, valid tenant data for tests."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Employee, Organization, Placement, QRPoint
from app.services.organization_service import create_organization
from app.services.qr_service import generate_unique_token


@dataclass
class Tenant:
    org: Organization
    branch: Branch
    employee: Employee
    point: QRPoint


@dataclass
class Team:
    """An org with two branches and one user per role (manager and employee in branch A)."""

    tenant: Tenant  # branch A, its employee and point
    branch_b: Branch
    employee_b: Employee
    point_b: QRPoint
    owner: str  # access tokens
    manager: str
    employee: str


async def add_branch(
    session: AsyncSession, org: Organization, name: str
) -> tuple[Branch, Employee, QRPoint]:
    branch = Branch(organization_id=org.id, name=name)
    session.add(branch)
    await session.flush()
    employee = Employee(organization_id=org.id, branch_id=branch.id, full_name=f"Мастер {name}")
    point = QRPoint(
        organization_id=org.id,
        branch_id=branch.id,
        name="Стойка",
        placement=Placement.COUNTER,
        token=await generate_unique_token(session),
    )
    session.add_all([employee, point])
    await session.flush()
    return branch, employee, point


async def make_tenant(session: AsyncSession, name: str = "Салон") -> Tenant:
    org = await create_organization(session, name=name, niche="beauty")
    branch, employee, point = await add_branch(session, org, f"{name}: филиал")
    return Tenant(org, branch, employee, point)
