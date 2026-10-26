"""AccessScope: who is acting, in which organization, and what they may see.

Every service that reads or writes business data takes a scope and filters through it.
Objects outside the scope are reported as "not found" (404), never as "forbidden".
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, false, true

from app.core.errors import NotFoundError, PermissionDeniedError
from app.models import Role


@dataclass(frozen=True)
class AccessScope:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: Role
    branch_id: uuid.UUID | None = None  # manager
    employee_id: uuid.UUID | None = None  # employee

    @property
    def is_owner(self) -> bool:
        return self.role is Role.OWNER

    def require_role(self, *roles: Role) -> None:
        if self.role not in roles:
            raise PermissionDeniedError()

    # --- checks on loaded objects -------------------------------------------------

    def can_see_branch(self, branch_id: uuid.UUID) -> bool:
        if self.role is Role.OWNER:
            return True
        if self.role is Role.MANAGER:
            return branch_id == self.branch_id
        return False

    def ensure_same_org(self, obj: Any) -> None:
        """Raise NotFound if a loaded object belongs to another organization."""
        if obj is None or obj.organization_id != self.organization_id:
            raise NotFoundError()

    def ensure_branch(self, obj: Any) -> None:
        """Org check + branch visibility for objects that have branch_id."""
        self.ensure_same_org(obj)
        if not self.can_see_branch(obj.branch_id):
            raise NotFoundError()

    # --- query filters --------------------------------------------------------------

    def org_filter(self, model: Any) -> ColumnElement[bool]:
        return model.organization_id == self.organization_id

    def branch_filter(self, branch_column: Any) -> ColumnElement[bool]:
        """Restrict rows with a branch_id column: managers see only their branch."""
        if self.role is Role.OWNER:
            return true()
        if self.role is Role.MANAGER:
            return branch_column == self.branch_id
        return false()

    def review_filters(self, review_model: Any) -> list[ColumnElement[bool]]:
        """Visibility of reviews (and therefore stats) for the current role."""
        filters = [self.org_filter(review_model)]
        if self.role is Role.MANAGER:
            filters.append(review_model.branch_id == self.branch_id)
        elif self.role is Role.EMPLOYEE:
            filters.append(review_model.employee_id == self.employee_id)
        return filters
