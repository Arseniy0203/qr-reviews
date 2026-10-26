import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import ArchivableMixin, Base, OrgScopedMixin, TimestampMixin, UUIDPkMixin


class Placement(enum.StrEnum):
    COUNTER = "counter"
    BUSINESS_CARD = "business_card"
    TABLE = "table"
    ONLINE = "online"
    OTHER = "other"


class QRPoint(UUIDPkMixin, OrgScopedMixin, ArchivableMixin, TimestampMixin, Base):
    """A review collection point. Not bound to an employee: see QRAssignment."""

    __tablename__ = "qr_points"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        ForeignKeyConstraint(
            ["branch_id", "organization_id"], ["branches.id", "branches.organization_id"]
        ),
        Index("ix_qr_points_org_branch", "organization_id", "branch_id"),
    )

    branch_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(String(200))
    placement: Mapped[Placement] = mapped_column(
        Enum(
            Placement,
            name="placement",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    # Public, unguessable token used in /r/{token}.
    token: Mapped[str] = mapped_column(String(16), unique=True)
    # Paused points show a stub page instead of the form.
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class QRAssignment(OrgScopedMixin, Base):
    """History of which employee a QR point was assigned to. Rows are never deleted."""

    __tablename__ = "qr_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["qr_point_id", "organization_id"], ["qr_points.id", "qr_points.organization_id"]
        ),
        ForeignKeyConstraint(
            ["employee_id", "organization_id"], ["employees.id", "employees.organization_id"]
        ),
        # At most one open assignment per point.
        Index(
            "uq_qr_assignments_open_per_point",
            "qr_point_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_qr_assignments_point_started", "qr_point_id", "started_at"),
        Index("ix_qr_assignments_employee", "employee_id"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="period_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    qr_point_id: Mapped[uuid.UUID]
    employee_id: Mapped[uuid.UUID]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
