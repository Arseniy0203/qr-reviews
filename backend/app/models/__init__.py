"""All models are imported here so Alembic and the mapper registry see every table."""

from app.models.base import Base
from app.models.branch import Branch
from app.models.category import ReviewCategory
from app.models.consent import ConsentDocument, ConsentType
from app.models.employee import Employee
from app.models.invitation import Invitation
from app.models.organization import Organization
from app.models.qr_point import Placement, QRAssignment, QRPoint
from app.models.review import (
    REVIEW_TEXT_MAX_LENGTH,
    Review,
    SuspiciousReason,
    review_category_links,
)
from app.models.telegram_link import TelegramLinkCode
from app.models.user import Membership, Role, User

__all__ = [
    "REVIEW_TEXT_MAX_LENGTH",
    "Base",
    "Branch",
    "ConsentDocument",
    "ConsentType",
    "Employee",
    "Invitation",
    "Membership",
    "Organization",
    "Placement",
    "QRAssignment",
    "QRPoint",
    "Review",
    "ReviewCategory",
    "Role",
    "SuspiciousReason",
    "TelegramLinkCode",
    "User",
    "review_category_links",
]
