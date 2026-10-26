import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import ManagerScope, SessionDep
from app.schemas.qr_point import (
    AssignmentOut,
    AssignmentRequest,
    PrintRequest,
    QRPointCreate,
    QRPointOut,
    QRPointUpdate,
)
from app.services import qr_service
from app.services.qr_service import MEDIA_TYPES, FileFormat

router = APIRouter(prefix="/qr-points", tags=["qr-points"])


def _attachment(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("")
async def list_points(
    scope: ManagerScope,
    session: SessionDep,
    branch_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> list[QRPointOut]:
    return await qr_service.list_points(
        session, scope, branch_id=branch_id, include_archived=include_archived
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_point(data: QRPointCreate, scope: ManagerScope, session: SessionDep) -> QRPointOut:
    return await qr_service.create_point(session, scope, data)


# Declared before /{point_id} routes so "print" is not parsed as an id.
@router.post("/print", response_class=Response)
async def print_sheet(data: PrintRequest, scope: ManagerScope, session: SessionDep) -> Response:
    content = await qr_service.render_print_sheet(session, scope, data.point_ids, data.layout)
    return _attachment(content, MEDIA_TYPES["pdf"], f"qr-codes-{data.layout}.pdf")


@router.get("/{point_id}")
async def get_point(point_id: uuid.UUID, scope: ManagerScope, session: SessionDep) -> QRPointOut:
    return await qr_service.get_point_out(session, scope, point_id)


@router.patch("/{point_id}")
async def update_point(
    point_id: uuid.UUID, data: QRPointUpdate, scope: ManagerScope, session: SessionDep
) -> QRPointOut:
    return await qr_service.update_point(session, scope, point_id, data)


@router.delete("/{point_id}")
async def archive_point(
    point_id: uuid.UUID, scope: ManagerScope, session: SessionDep
) -> QRPointOut:
    """Soft delete (archive): the link shows a stub page, history and reviews are kept."""
    return await qr_service.set_archived(session, scope, point_id, archived=True)


@router.post("/{point_id}/restore")
async def restore_point(
    point_id: uuid.UUID, scope: ManagerScope, session: SessionDep
) -> QRPointOut:
    return await qr_service.set_archived(session, scope, point_id, archived=False)


@router.put("/{point_id}/assignment")
async def assign_employee(
    point_id: uuid.UUID, data: AssignmentRequest, scope: ManagerScope, session: SessionDep
) -> QRPointOut:
    return await qr_service.assign_employee(session, scope, point_id, data.employee_id)


@router.get("/{point_id}/assignments")
async def list_assignments(
    point_id: uuid.UUID, scope: ManagerScope, session: SessionDep
) -> list[AssignmentOut]:
    return await qr_service.list_assignments(session, scope, point_id)


@router.get("/{point_id}/download", response_class=Response)
async def download(
    point_id: uuid.UUID,
    scope: ManagerScope,
    session: SessionDep,
    format: FileFormat = "png",  # public query parameter name
    caption: bool = True,
) -> Response:
    content, filename = await qr_service.render_point_file(
        session, scope, point_id, format, caption
    )
    return _attachment(content, MEDIA_TYPES[format], filename)
