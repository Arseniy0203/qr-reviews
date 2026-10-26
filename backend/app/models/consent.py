import enum
from datetime import datetime

from sqlalchemy import Enum, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, OrgScopedMixin, UUIDPkMixin


class ConsentType(enum.StrEnum):
    PD = "pd"  # personal data processing, 152-FZ (a separate document since 01.09.2025)
    MARKETING = "marketing"  # advertising messages, 38-FZ art. 18


class ConsentDocument(UUIDPkMixin, OrgScopedMixin, CreatedAtMixin, Base):
    """A published, immutable consent text of one organization (the PD operator).

    Editing means publishing a new version. A DB trigger rejects UPDATE/DELETE, so a
    review always points at the exact text the customer agreed to.
    """

    __tablename__ = "consent_documents"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("organization_id", "type", "version"),
    )

    type: Mapped[ConsentType] = mapped_column(
        Enum(
            ConsentType,
            name="consent_type",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(server_default=func.now())
