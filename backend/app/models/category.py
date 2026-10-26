from sqlalchemy import Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import ArchivableMixin, Base, OrgScopedMixin, TimestampMixin, UUIDPkMixin


class ReviewCategory(UUIDPkMixin, OrgScopedMixin, ArchivableMixin, TimestampMixin, Base):
    """Organization-owned review category; seeded from the niche template, then editable."""

    __tablename__ = "review_categories"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        Index("ix_review_categories_org_sort", "organization_id", "sort_order"),
        Index(
            "uq_review_categories_org_name_active",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(100))
    # Stable identifier from the niche template (e.g. "booking"); NULL for custom categories.
    code: Mapped[str | None] = mapped_column(String(50), default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
