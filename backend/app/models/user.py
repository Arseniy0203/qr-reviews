import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPkMixin


class Role(enum.StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    EMPLOYEE = "employee"


def role_enum() -> Enum:
    return Enum(
        Role,
        name="role",
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [m.value for m in e],
    )


# owner: whole org; manager: exactly one branch; employee: linked to an Employee record.
ROLE_SCOPE_CHECK = (
    "(role = 'owner' AND branch_id IS NULL AND employee_id IS NULL)"
    " OR (role = 'manager' AND branch_id IS NOT NULL AND employee_id IS NULL)"
    " OR (role = 'employee' AND employee_id IS NOT NULL)"
)


class User(UUIDPkMixin, TimestampMixin, Base):
    """A dashboard account. Global (not org-scoped): access is granted via Membership."""

    __tablename__ = "users"

    # Stored lowercased by the auth service.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, default=None)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user", lazy="raise")

    @property
    def telegram_linked(self) -> bool:
        return self.telegram_chat_id is not None


class Membership(UUIDPkMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id"),
        ForeignKeyConstraint(
            ["branch_id", "organization_id"], ["branches.id", "branches.organization_id"]
        ),
        ForeignKeyConstraint(
            ["employee_id", "organization_id"], ["employees.id", "employees.organization_id"]
        ),
        Index("ix_memberships_org_role", "organization_id", "role"),
        CheckConstraint(ROLE_SCOPE_CHECK, name="role_scope"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[Role] = mapped_column(role_enum())
    branch_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(default=None)

    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise")
