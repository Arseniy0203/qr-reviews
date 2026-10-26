"""Role permissions, invitations and organization isolation at the API level."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentDocument, ConsentType, Invitation, Role
from tests.api_helpers import add_member, bearer, login, owner_token
from tests.factories import Tenant, make_tenant


@dataclass
class Team:
    tenant: Tenant
    owner: str
    manager: str
    employee: str


@pytest.fixture
async def team(session: AsyncSession, client: httpx.AsyncClient) -> Team:
    tenant = await make_tenant(session, "Лаванда")
    await session.commit()
    org_id = tenant.org.id
    for role, email, extra in [
        (Role.OWNER, "owner@team.ru", {}),
        (Role.MANAGER, "manager@team.ru", {"branch_id": tenant.branch.id}),
        (Role.EMPLOYEE, "employee@team.ru", {"employee_id": tenant.employee.id}),
    ]:
        await add_member(session, org_id, role, email=email, **extra)
    return Team(
        tenant=tenant,
        owner=await login(client, "owner@team.ru"),
        manager=await login(client, "manager@team.ru"),
        employee=await login(client, "employee@team.ru"),
    )


# --- organization endpoints -------------------------------------------------------


async def test_every_role_can_read_own_organization(client: httpx.AsyncClient, team: Team) -> None:
    for token in (team.owner, team.manager, team.employee):
        response = await client.get("/api/v1/organization", headers=bearer(token))
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Лаванда"
        assert body["employee_title"] == "мастер"
        assert body["settings"]["timezone"] == "Europe/Moscow"


async def test_only_owner_updates_organization(client: httpx.AsyncClient, team: Team) -> None:
    payload = {"settings": {"negative_threshold": 2, "collect_contact": True}}
    for token in (team.manager, team.employee):
        response = await client.patch("/api/v1/organization", json=payload, headers=bearer(token))
        assert response.status_code == 403

    response = await client.patch("/api/v1/organization", json=payload, headers=bearer(team.owner))
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["negative_threshold"] == 2
    assert settings["collect_contact"] is True
    assert settings["timezone"] == "Europe/Moscow"  # untouched fields are kept


async def test_settings_update_validation(client: httpx.AsyncClient, team: Team) -> None:
    for bad in (
        {"timezone": "Mars/Base"},
        {"negative_threshold": 7},
        {"negative_threshold": None},
        {"inn": "123"},
    ):
        response = await client.patch(
            "/api/v1/organization", json={"settings": bad}, headers=bearer(team.owner)
        )
        assert response.status_code == 422, bad


async def test_changing_legal_details_publishes_new_consent_version(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    response = await client.patch(
        "/api/v1/organization",
        json={"settings": {"legal_name": "ООО «Лаванда»", "inn": "7701234567"}},
        headers=bearer(team.owner),
    )
    assert response.status_code == 200
    documents = (
        await session.scalars(
            select(ConsentDocument)
            .where(
                ConsentDocument.organization_id == team.tenant.org.id,
                ConsentDocument.type == ConsentType.PD,
            )
            .order_by(ConsentDocument.version)
        )
    ).all()
    assert [d.version for d in documents] == [1, 2]
    assert "ООО «Лаванда», ИНН 7701234567" in documents[1].text
    assert "ИНН" not in documents[0].text  # the old version is kept as it was


async def test_members_list_owner_only(client: httpx.AsyncClient, team: Team) -> None:
    assert (
        await client.get("/api/v1/organization/members", headers=bearer(team.manager))
    ).status_code == 403
    response = await client.get("/api/v1/organization/members", headers=bearer(team.owner))
    assert response.status_code == 200
    assert {m["role"] for m in response.json()} == {"owner", "manager", "employee"}


# --- invitations ------------------------------------------------------------------


async def test_owner_invites_manager_who_registers_by_link(
    client: httpx.AsyncClient, team: Team
) -> None:
    created = await client.post(
        "/api/v1/invitations",
        json={"role": "manager", "branch_id": str(team.tenant.branch.id)},
        headers=bearer(team.owner),
    )
    assert created.status_code == 201
    url = created.json()["url"]
    assert url.startswith("http://localhost:8080/invite/")
    token = url.rsplit("/", 1)[1]

    preview = await client.get(f"/api/v1/invitations/by-token/{token}")
    assert preview.status_code == 200
    assert preview.json()["organization_name"] == "Лаванда"
    assert preview.json()["branch_name"] == "Лаванда: филиал"

    accepted = await client.post(
        f"/api/v1/invitations/by-token/{token}/accept",
        json={"email": "new.manager@team.ru", "full_name": "Новый", "password": "secret-pass-1"},
    )
    assert accepted.status_code == 200
    me = await client.get("/api/v1/auth/me", headers=bearer(accepted.json()["access_token"]))
    membership = me.json()["memberships"][0]
    assert membership["role"] == "manager"
    assert membership["branch_id"] == str(team.tenant.branch.id)

    # A link works only once.
    again = await client.post(
        f"/api/v1/invitations/by-token/{token}/accept",
        json={"email": "other@team.ru", "full_name": "Ещё", "password": "secret-pass-1"},
    )
    assert again.status_code == 410


async def test_existing_user_accepts_while_logged_in(client: httpx.AsyncClient, team: Team) -> None:
    other_owner = await owner_token(client, "solo@team.ru")
    created = await client.post(
        "/api/v1/invitations",
        json={"role": "employee", "employee_id": str(team.tenant.employee.id)},
        headers=bearer(team.owner),
    )
    token = created.json()["url"].rsplit("/", 1)[1]
    accepted = await client.post(
        f"/api/v1/invitations/by-token/{token}/accept", json={}, headers=bearer(other_owner)
    )
    assert accepted.status_code == 200
    me = await client.get("/api/v1/auth/me", headers=bearer(other_owner))
    assert {m["role"] for m in me.json()["memberships"]} == {"owner", "employee"}


async def test_new_user_with_existing_email_must_log_in(
    client: httpx.AsyncClient, team: Team
) -> None:
    created = await client.post(
        "/api/v1/invitations",
        json={"role": "manager", "branch_id": str(team.tenant.branch.id)},
        headers=bearer(team.owner),
    )
    token = created.json()["url"].rsplit("/", 1)[1]
    response = await client.post(
        f"/api/v1/invitations/by-token/{token}/accept",
        json={"email": "manager@team.ru", "full_name": "Двойник", "password": "secret-pass-1"},
    )
    assert response.status_code == 409


async def test_only_owner_manages_invitations(client: httpx.AsyncClient, team: Team) -> None:
    payload = {"role": "manager", "branch_id": str(team.tenant.branch.id)}
    for token in (team.manager, team.employee):
        assert (
            await client.post("/api/v1/invitations", json=payload, headers=bearer(token))
        ).status_code == 403
        assert (await client.get("/api/v1/invitations", headers=bearer(token))).status_code == 403


async def test_invitation_role_requires_target(client: httpx.AsyncClient, team: Team) -> None:
    response = await client.post(
        "/api/v1/invitations", json={"role": "manager"}, headers=bearer(team.owner)
    )
    assert response.status_code == 400
    owner_invite = await client.post(
        "/api/v1/invitations", json={"role": "owner"}, headers=bearer(team.owner)
    )
    assert owner_invite.status_code == 422


async def test_revoked_and_expired_invitations_are_invalid(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    async def make() -> tuple[str, str]:
        created = await client.post(
            "/api/v1/invitations",
            json={"role": "manager", "branch_id": str(team.tenant.branch.id)},
            headers=bearer(team.owner),
        )
        return created.json()["id"], created.json()["url"].rsplit("/", 1)[1]

    revoked_id, revoked_token = await make()
    assert (
        await client.delete(f"/api/v1/invitations/{revoked_id}", headers=bearer(team.owner))
    ).status_code == 204
    assert (await client.get(f"/api/v1/invitations/by-token/{revoked_token}")).status_code == 410

    expired_id, expired_token = await make()
    await session.execute(
        update(Invitation)
        .where(Invitation.id == expired_id)
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await session.commit()
    assert (await client.get(f"/api/v1/invitations/by-token/{expired_token}")).status_code == 410
    assert (await client.get("/api/v1/invitations/by-token/nonsense")).status_code == 410


# --- isolation between organizations ------------------------------------------------


async def test_cannot_invite_to_branch_of_another_org(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    stranger = await make_tenant(session, "Чужой салон")
    await session.commit()
    response = await client.post(
        "/api/v1/invitations",
        json={"role": "manager", "branch_id": str(stranger.branch.id)},
        headers=bearer(team.owner),
    )
    assert response.status_code == 404


async def test_cannot_revoke_invitation_of_another_org(
    client: httpx.AsyncClient, team: Team
) -> None:
    created = await client.post(
        "/api/v1/invitations",
        json={"role": "manager", "branch_id": str(team.tenant.branch.id)},
        headers=bearer(team.owner),
    )
    intruder = await owner_token(client, "intruder@example.com")
    response = await client.delete(
        f"/api/v1/invitations/{created.json()['id']}", headers=bearer(intruder)
    )
    assert response.status_code == 404


async def test_foreign_organization_header_is_rejected(
    client: httpx.AsyncClient, team: Team
) -> None:
    intruder = await owner_token(client, "intruder@example.com")
    response = await client.get(
        "/api/v1/organization",
        headers={**bearer(intruder), "X-Organization-Id": str(team.tenant.org.id)},
    )
    assert response.status_code == 403

    own = await client.get("/api/v1/organization", headers=bearer(intruder))
    assert own.status_code == 200
    assert own.json()["id"] != str(team.tenant.org.id)
