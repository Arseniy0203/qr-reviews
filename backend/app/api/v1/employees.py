import uuid

from fastapi import APIRouter, status

from app.api.deps import ManagerScope, ScopeDep, SessionDep
from app.schemas.employee import EmployeeCreate, EmployeeOut, EmployeeUpdate
from app.services import employee_service

router = APIRouter(prefix="/employees", tags=["employees"])


@router.get("")
async def list_employees(
    scope: ScopeDep,
    session: SessionDep,
    branch_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> list[EmployeeOut]:
    employees = await employee_service.list_employees(
        session, scope, branch_id=branch_id, include_archived=include_archived
    )
    return [EmployeeOut.model_validate(e) for e in employees]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_employee(
    data: EmployeeCreate, scope: ManagerScope, session: SessionDep
) -> EmployeeOut:
    return EmployeeOut.model_validate(await employee_service.create_employee(session, scope, data))


@router.get("/{employee_id}")
async def get_employee(employee_id: uuid.UUID, scope: ScopeDep, session: SessionDep) -> EmployeeOut:
    employee = await employee_service.get_employee(session, scope, employee_id)
    return EmployeeOut.model_validate(employee)


@router.patch("/{employee_id}")
async def update_employee(
    employee_id: uuid.UUID, data: EmployeeUpdate, scope: ManagerScope, session: SessionDep
) -> EmployeeOut:
    employee = await employee_service.update_employee(session, scope, employee_id, data)
    return EmployeeOut.model_validate(employee)


@router.delete("/{employee_id}")
async def archive_employee(
    employee_id: uuid.UUID, scope: ManagerScope, session: SessionDep
) -> EmployeeOut:
    """Soft delete (archive); closes the employee's open QR assignments."""
    employee = await employee_service.archive_employee(session, scope, employee_id)
    return EmployeeOut.model_validate(employee)


@router.post("/{employee_id}/restore")
async def restore_employee(
    employee_id: uuid.UUID, scope: ManagerScope, session: SessionDep
) -> EmployeeOut:
    employee = await employee_service.restore_employee(session, scope, employee_id)
    return EmployeeOut.model_validate(employee)
