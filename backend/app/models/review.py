import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    SmallInteger,
    String,
    Table,
    Text,
    false,
    func,
)
from sqlalchemy import text as sql_text  # `text` is also a Review column name
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, OrgScopedMixin
from app.models.category import ReviewCategory

REVIEW_TEXT_MAX_LENGTH = 2000


class SuspiciousReason(enum.StrEnum):
    """Codes stored in Review.suspicious_reasons."""

    DEVICE_REPEAT = "device_repeat"  # same device, same point within the window
    IP_REPEAT = "ip_repeat"  # many reviews from one IP (hard threshold, or soft + other signal)
    POINT_BURST = "point_burst"  # too many reviews on one point in a short window
    EMPLOYEE_BURST = "employee_burst"  # too many reviews for one employee in a short window
    DUPLICATE_TEXT = "duplicate_text"  # near-identical text (simhash)


review_category_links = Table(
    "review_category_links",
    Base.metadata,
    Column(
        "review_id",
        BigInteger,
        ForeignKey("reviews.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "category_id",
        ForeignKey("review_categories.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    ),
)


class Review(OrgScopedMixin, Base):
    __tablename__ = "reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["branch_id", "organization_id"], ["branches.id", "branches.organization_id"]
        ),
        ForeignKeyConstraint(
            ["qr_point_id", "organization_id"], ["qr_points.id", "qr_points.organization_id"]
        ),
        ForeignKeyConstraint(
            ["employee_id", "organization_id"], ["employees.id", "employees.organization_id"]
        ),
        # Stats queries: org-wide, per branch, per employee, per point — all by time range.
        Index("ix_reviews_org_created", "organization_id", "created_at"),
        Index("ix_reviews_org_branch_created", "organization_id", "branch_id", "created_at"),
        Index("ix_reviews_org_employee_created", "organization_id", "employee_id", "created_at"),
        Index("ix_reviews_point_created", "qr_point_id", "created_at"),
        # Repeat customers and consent revocation look reviews up by phone.
        Index(
            "ix_reviews_org_phone",
            "organization_id",
            "customer_phone",
            postgresql_where=sql_text("customer_phone IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["pd_consent_document_id", "organization_id"],
            ["consent_documents.id", "consent_documents.organization_id"],
        ),
        ForeignKeyConstraint(
            ["marketing_consent_document_id", "organization_id"],
            ["consent_documents.id", "consent_documents.organization_id"],
        ),
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
        CheckConstraint(f"char_length(text) <= {REVIEW_TEXT_MAX_LENGTH}", name="text_length"),
        CheckConstraint(r"customer_phone ~ '^\+[0-9]{10,15}$'", name="phone_e164"),
        # A contact may exist only under a valid (not revoked) PD consent.
        CheckConstraint(
            "(customer_name IS NULL AND customer_phone IS NULL)"
            " OR (pd_consent_document_id IS NOT NULL AND pd_consent_revoked_at IS NULL)",
            name="contact_requires_consent",
        ),
        CheckConstraint(
            "(pd_consent_document_id IS NULL) = (pd_consent_at IS NULL)",
            name="pd_consent_complete",
        ),
        CheckConstraint(
            "(marketing_consent_document_id IS NULL) = (marketing_consent_at IS NULL)",
            name="marketing_consent_complete",
        ),
        # Marketing messages need a phone, and a phone needs PD consent.
        CheckConstraint(
            "marketing_consent_document_id IS NULL OR pd_consent_document_id IS NOT NULL",
            name="marketing_requires_pd",
        ),
        CheckConstraint(
            "pd_consent_revoked_at IS NULL OR pd_consent_at IS NOT NULL",
            name="pd_revoke_after_consent",
        ),
        CheckConstraint(
            "marketing_consent_revoked_at IS NULL OR marketing_consent_at IS NOT NULL",
            name="marketing_revoke_after_consent",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    branch_id: Mapped[uuid.UUID]
    qr_point_id: Mapped[uuid.UUID]
    # Employee assigned to the point at the moment of the review (denormalized on purpose).
    employee_id: Mapped[uuid.UUID | None] = mapped_column(default=None)

    rating: Mapped[int] = mapped_column(SmallInteger)
    text: Mapped[str | None] = mapped_column(Text, default=None)

    customer_name: Mapped[str | None] = mapped_column(String(100), default=None)
    # E.164, normalized by services.phone.normalize_phone.
    customer_phone: Mapped[str | None] = mapped_column(String(16), default=None)

    # Consents (152-FZ / 38-FZ): the exact document version and the moment of agreement.
    # Kept after revocation as evidence; revocation wipes the contact and sets *_revoked_at.
    pd_consent_document_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    pd_consent_at: Mapped[datetime | None] = mapped_column(default=None)
    pd_consent_revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    marketing_consent_document_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    marketing_consent_at: Mapped[datetime | None] = mapped_column(default=None)
    marketing_consent_revoked_at: Mapped[datetime | None] = mapped_column(default=None)

    is_suspicious: Mapped[bool] = mapped_column(default=False, server_default=false())
    suspicious_reasons: Mapped[list[str]] = mapped_column(
        default=list, server_default=sql_text("'[]'::jsonb")
    )
    # HMAC-SHA256 hex digests; raw IP / device id are never stored.
    device_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    ip_hash: Mapped[str | None] = mapped_column(String(64), default=None)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    categories: Mapped[list[ReviewCategory]] = relationship(
        secondary=review_category_links, lazy="raise"
    )
