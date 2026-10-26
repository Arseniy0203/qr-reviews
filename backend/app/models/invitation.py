import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPkMixin
from app.models.user import ROLE_SCOPE_CHECK, Role, role_enum


class Invitation(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    """Copyable invitation link for a manager/employee. Only a hash of the token is stored."""

    __tablename__ = "invitations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["branch_id", "organization_id"], ["branches.id", "branches.organization_id"]
        ),
        ForeignKeyConstraint(
            ["employee_id", "organization_id"], ["employees.id", "employees.organization_id"]
        ),
        Index("ix_invitations_org_created", "organization_id", "created_at"),
        CheckConstraint(ROLE_SCOPE_CHECK, name="role_scope"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[Role] = mapped_column(role_enum())
    branch_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None] = mapped_column(default=None)
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
