import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class EmployeeCreate(BaseModel):
    branch_id: uuid.UUID
    full_name: str = Field(min_length=1, max_length=200)
    position: str | None = Field(default=None, max_length=100)


class EmployeeUpdate(BaseModel):
    branch_id: uuid.UUID | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    position: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class EmployeeOut(ORMModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    full_name: str
    position: str | None
    is_active: bool
    archived_at: datetime | None
    created_at: datetime
