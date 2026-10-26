from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import ArchivableMixin, Base, OrgScopedMixin, TimestampMixin, UUIDPkMixin


class Branch(UUIDPkMixin, OrgScopedMixin, ArchivableMixin, TimestampMixin, Base):
    __tablename__ = "branches"
    __table_args__ = (
        # Target for composite FKs (branch_id, organization_id): children cannot point
        # at a branch of another organization, enforced by the database itself.
        UniqueConstraint("id", "organization_id"),
        Index("ix_branches_org", "organization_id"),
    )

    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(500), default=None)
