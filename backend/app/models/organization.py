from typing import Any

from sqlalchemy import String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin


class Organization(UUIDPkMixin, TimestampMixin, Base):
    """A client company (tenant). `settings` is validated by `schemas.organization.OrgSettings`."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    niche: Mapped[str] = mapped_column(String(50))
    settings: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
