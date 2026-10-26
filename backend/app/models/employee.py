import uuid

from sqlalchemy import ForeignKeyConstraint, Index, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import ArchivableMixin, Base, OrgScopedMixin, TimestampMixin, UUIDPkMixin


class Employee(UUIDPkMixin, OrgScopedMixin, ArchivableMixin, TimestampMixin, Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        ForeignKeyConstraint(
            ["branch_id", "organization_id"], ["branches.id", "branches.organization_id"]
        ),
        Index("ix_employees_org_branch", "organization_id", "branch_id"),
    )

    branch_id: Mapped[uuid.UUID]
    full_name: Mapped[str] = mapped_column(String(200))
    position: Mapped[str | None] = mapped_column(String(100), default=None)
    # Temporarily inactive (vacation, dismissed) — still visible in lists and stats.
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
