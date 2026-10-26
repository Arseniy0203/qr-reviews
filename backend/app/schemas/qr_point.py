import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import Placement

PrintLayout = Literal["1x1", "2x3", "3x4"]


class QRPointCreate(BaseModel):
    branch_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    placement: Placement
    employee_id: uuid.UUID | None = None


class QRPointUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    placement: Placement | None = None
    is_active: bool | None = None


class AssignmentRequest(BaseModel):
    """null employee_id unassigns the point."""

    employee_id: uuid.UUID | None


class EmployeeRef(BaseModel):
    id: uuid.UUID
    full_name: str


class QRPointOut(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str
    name: str
    placement: Placement
    token: str
    url: str
    is_active: bool
    archived_at: datetime | None
    created_at: datetime
    current_employee: EmployeeRef | None


class AssignmentOut(BaseModel):
    id: int
    employee: EmployeeRef
    started_at: datetime
    ended_at: datetime | None


class PrintRequest(BaseModel):
    point_ids: list[uuid.UUID] = Field(min_length=1, max_length=300)
    layout: PrintLayout = "2x3"
