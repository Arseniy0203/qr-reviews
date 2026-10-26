import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models import Role
from app.schemas.common import ORMModel

PasswordStr = Field(min_length=8, max_length=128)


def _normalize_email(value: str) -> str:
    return value.strip().lower()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = PasswordStr
    full_name: str = Field(min_length=1, max_length=200)
    organization_name: str = Field(min_length=1, max_length=200)
    niche: str = Field(default="beauty", max_length=50)

    _email = field_validator("email")(_normalize_email)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    _email = field_validator("email")(_normalize_email)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    telegram_linked: bool


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role: Role
    branch_id: uuid.UUID | None
    employee_id: uuid.UUID | None


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


# --- Invitations ---------------------------------------------------------------


class InvitationCreate(BaseModel):
    role: Literal[Role.MANAGER, Role.EMPLOYEE]
    branch_id: uuid.UUID | None = None
    employee_id: uuid.UUID | None = None
    email: EmailStr | None = None

    _email = field_validator("email")(lambda v: _normalize_email(v) if v else v)


class InvitationOut(ORMModel):
    id: uuid.UUID
    role: Role
    branch_id: uuid.UUID | None
    employee_id: uuid.UUID | None
    email: str | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationCreated(InvitationOut):
    # The raw token exists only in this response; the DB stores its hash.
    url: str


class InvitationPreview(BaseModel):
    organization_name: str
    role: Role
    branch_name: str | None
    employee_name: str | None
    email: str | None
    expires_at: datetime


class AcceptInvitationRequest(BaseModel):
    """Body for new users. Authenticated users accept with an empty body."""

    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    password: str | None = Field(default=None, min_length=8, max_length=128)

    _email = field_validator("email")(lambda v: _normalize_email(v) if v else v)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    branch_id: uuid.UUID | None
    employee_id: uuid.UUID | None
    telegram_linked: bool
