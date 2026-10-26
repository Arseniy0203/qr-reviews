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


async def make_tenant(session: AsyncSession, name: str = "Салон") -> Tenant:
    org = await create_organization(session, name=name, niche="beauty")
    branch = Branch(organization_id=org.id, name=f"{name}: филиал")
    session.add(branch)
    await session.flush()
    employee = Employee(organization_id=org.id, branch_id=branch.id, full_name="Мастер")
    point = QRPoint(
        organization_id=org.id,
        branch_id=branch.id,
        name="Стойка",
        placement=Placement.COUNTER,
        token=await generate_unique_token(session),
    )
    session.add_all([employee, point])
    await session.flush()
    return Tenant(org, branch, employee, point)
