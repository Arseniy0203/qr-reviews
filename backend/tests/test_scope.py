import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import NotFoundError, PermissionDeniedError
from app.models import Review, Role
from app.services.scope import AccessScope

ORG = uuid.uuid4()
BRANCH = uuid.uuid4()
OTHER_BRANCH = uuid.uuid4()
EMPLOYEE = uuid.uuid4()


def scope(role: Role, **kwargs: uuid.UUID) -> AccessScope:
    return AccessScope(user_id=uuid.uuid4(), organization_id=ORG, role=role, **kwargs)


def test_branch_visibility_by_role() -> None:
    assert scope(Role.OWNER).can_see_branch(OTHER_BRANCH)
    manager = scope(Role.MANAGER, branch_id=BRANCH)
    assert manager.can_see_branch(BRANCH)
    assert not manager.can_see_branch(OTHER_BRANCH)
    assert not scope(Role.EMPLOYEE, employee_id=EMPLOYEE).can_see_branch(BRANCH)


def test_foreign_objects_are_not_found() -> None:
    owner = scope(Role.OWNER)
    with pytest.raises(NotFoundError):
        owner.ensure_same_org(SimpleNamespace(organization_id=uuid.uuid4()))
    with pytest.raises(NotFoundError):
        owner.ensure_same_org(None)
    manager = scope(Role.MANAGER, branch_id=BRANCH)
    with pytest.raises(NotFoundError):
        manager.ensure_branch(SimpleNamespace(organization_id=ORG, branch_id=OTHER_BRANCH))
    manager.ensure_branch(SimpleNamespace(organization_id=ORG, branch_id=BRANCH))


def test_require_role() -> None:
    with pytest.raises(PermissionDeniedError):
        scope(Role.MANAGER, branch_id=BRANCH).require_role(Role.OWNER)
    scope(Role.OWNER).require_role(Role.OWNER, Role.MANAGER)


def test_review_filters_narrow_by_role() -> None:
    def compiled(s: AccessScope) -> str:
        return " AND ".join(
            str(f.compile(compile_kwargs={"literal_binds": True})) for f in s.review_filters(Review)
        )

    assert "branch_id" not in compiled(scope(Role.OWNER))
    assert BRANCH.hex in compiled(scope(Role.MANAGER, branch_id=BRANCH)).replace("-", "")
    employee_sql = compiled(scope(Role.EMPLOYEE, employee_id=EMPLOYEE))
    assert "employee_id" in employee_sql
    assert "branch_id" not in employee_sql
