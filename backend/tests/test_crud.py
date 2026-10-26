"""Branches, employees, categories: permissions, isolation, soft delete."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api_helpers import bearer, owner_token
from tests.factories import Team

# --- branches ---------------------------------------------------------------------


async def test_branch_lists_by_role(client: httpx.AsyncClient, team: Team) -> None:
    owner = await client.get("/api/v1/branches", headers=bearer(team.owner))
    assert {b["name"] for b in owner.json()} == {"Лаванда: филиал", "Филиал Б"}

    manager = await client.get("/api/v1/branches", headers=bearer(team.manager))
    assert [b["id"] for b in manager.json()] == [str(team.tenant.branch.id)]

    employee = await client.get("/api/v1/branches", headers=bearer(team.employee))
    assert employee.json() == []

    hidden = await client.get(f"/api/v1/branches/{team.branch_b.id}", headers=bearer(team.manager))
    assert hidden.status_code == 404


async def test_branch_crud_owner_only(client: httpx.AsyncClient, team: Team) -> None:
    payload = {"name": "Новый", "address": "ул. Ленина, 1"}
    assert (
        await client.post("/api/v1/branches", json=payload, headers=bearer(team.manager))
    ).status_code == 403

    created = await client.post("/api/v1/branches", json=payload, headers=bearer(team.owner))
    assert created.status_code == 201
    branch_id = created.json()["id"]

    renamed = await client.patch(
        f"/api/v1/branches/{branch_id}", json={"name": "Переименован"}, headers=bearer(team.owner)
    )
    assert renamed.json()["name"] == "Переименован"
    assert renamed.json()["address"] == "ул. Ленина, 1"


async def test_archiving_branch_archives_its_points(client: httpx.AsyncClient, team: Team) -> None:
    point_id = str(team.point_b.id)
    await client.put(
        f"/api/v1/qr-points/{point_id}/assignment",
        json={"employee_id": str(team.employee_b.id)},
        headers=bearer(team.owner),
    )
    archived = await client.delete(
        f"/api/v1/branches/{team.branch_b.id}", headers=bearer(team.owner)
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    listed = await client.get("/api/v1/branches", headers=bearer(team.owner))
    assert str(team.branch_b.id) not in {b["id"] for b in listed.json()}
    with_archived = await client.get(
        "/api/v1/branches?include_archived=true", headers=bearer(team.owner)
    )
    assert str(team.branch_b.id) in {b["id"] for b in with_archived.json()}

    point = await client.get(f"/api/v1/qr-points/{point_id}", headers=bearer(team.owner))
    assert point.json()["archived_at"] is not None
    assert point.json()["current_employee"] is None
    history = await client.get(
        f"/api/v1/qr-points/{point_id}/assignments", headers=bearer(team.owner)
    )
    assert len(history.json()) == 1  # history is kept, the row is closed
    assert history.json()[0]["ended_at"] is not None

    # No new employees or points in an archived branch.
    response = await client.post(
        "/api/v1/employees",
        json={"branch_id": str(team.branch_b.id), "full_name": "Новенький"},
        headers=bearer(team.owner),
    )
    assert response.status_code == 400


async def test_branch_of_another_org_is_invisible(client: httpx.AsyncClient, team: Team) -> None:
    intruder = await owner_token(client, "intruder@example.com")
    for method, url in [
        ("GET", f"/api/v1/branches/{team.tenant.branch.id}"),
        ("PATCH", f"/api/v1/branches/{team.tenant.branch.id}"),
        ("DELETE", f"/api/v1/branches/{team.tenant.branch.id}"),
    ]:
        response = await client.request(
            method, url, json={"name": "x"} if method == "PATCH" else None, headers=bearer(intruder)
        )
        assert response.status_code == 404, (method, url)


# --- employees --------------------------------------------------------------------


async def test_employee_lists_by_role(client: httpx.AsyncClient, team: Team) -> None:
    owner = await client.get("/api/v1/employees", headers=bearer(team.owner))
    assert len(owner.json()) == 2
    by_branch = await client.get(
        f"/api/v1/employees?branch_id={team.branch_b.id}", headers=bearer(team.owner)
    )
    assert [e["id"] for e in by_branch.json()] == [str(team.employee_b.id)]

    manager = await client.get("/api/v1/employees", headers=bearer(team.manager))
    assert [e["id"] for e in manager.json()] == [str(team.tenant.employee.id)]

    employee = await client.get("/api/v1/employees", headers=bearer(team.employee))
    assert [e["id"] for e in employee.json()] == [str(team.tenant.employee.id)]
    other = await client.get(
        f"/api/v1/employees/{team.employee_b.id}", headers=bearer(team.employee)
    )
    assert other.status_code == 404


async def test_manager_manages_employees_of_own_branch_only(
    client: httpx.AsyncClient, team: Team
) -> None:
    own = await client.post(
        "/api/v1/employees",
        json={"branch_id": str(team.tenant.branch.id), "full_name": "Ирина", "position": "Бровист"},
        headers=bearer(team.manager),
    )
    assert own.status_code == 201

    foreign = await client.post(
        "/api/v1/employees",
        json={"branch_id": str(team.branch_b.id), "full_name": "Чужой"},
        headers=bearer(team.manager),
    )
    assert foreign.status_code == 404

    move_out = await client.patch(
        f"/api/v1/employees/{own.json()['id']}",
        json={"branch_id": str(team.branch_b.id)},
        headers=bearer(team.manager),
    )
    assert move_out.status_code == 404

    edit_foreign = await client.patch(
        f"/api/v1/employees/{team.employee_b.id}",
        json={"full_name": "Взлом"},
        headers=bearer(team.manager),
    )
    assert edit_foreign.status_code == 404

    employee_cannot_create = await client.post(
        "/api/v1/employees",
        json={"branch_id": str(team.tenant.branch.id), "full_name": "Сам себе"},
        headers=bearer(team.employee),
    )
    assert employee_cannot_create.status_code == 403


async def test_archive_employee_closes_assignments_and_hides(
    client: httpx.AsyncClient, team: Team
) -> None:
    point_id = str(team.tenant.point.id)
    employee_id = str(team.tenant.employee.id)
    await client.put(
        f"/api/v1/qr-points/{point_id}/assignment",
        json={"employee_id": employee_id},
        headers=bearer(team.owner),
    )
    archived = await client.delete(f"/api/v1/employees/{employee_id}", headers=bearer(team.manager))
    assert archived.status_code == 200

    point = await client.get(f"/api/v1/qr-points/{point_id}", headers=bearer(team.owner))
    assert point.json()["current_employee"] is None
    listed = await client.get("/api/v1/employees", headers=bearer(team.owner))
    assert employee_id not in {e["id"] for e in listed.json()}

    restored = await client.post(
        f"/api/v1/employees/{employee_id}/restore", headers=bearer(team.owner)
    )
    assert restored.json()["archived_at"] is None


async def test_deactivate_employee_keeps_assignment(client: httpx.AsyncClient, team: Team) -> None:
    point_id = str(team.tenant.point.id)
    employee_id = str(team.tenant.employee.id)
    await client.put(
        f"/api/v1/qr-points/{point_id}/assignment",
        json={"employee_id": employee_id},
        headers=bearer(team.owner),
    )
    response = await client.patch(
        f"/api/v1/employees/{employee_id}", json={"is_active": False}, headers=bearer(team.owner)
    )
    assert response.json()["is_active"] is False
    point = await client.get(f"/api/v1/qr-points/{point_id}", headers=bearer(team.owner))
    assert point.json()["current_employee"]["id"] == employee_id


# --- categories -------------------------------------------------------------------


async def test_categories_seeded_from_niche_and_editable(
    client: httpx.AsyncClient, team: Team
) -> None:
    listed = await client.get("/api/v1/categories", headers=bearer(team.employee))
    names = [c["name"] for c in listed.json()]
    assert names == ["Качество работы", "Мастер", "Запись", "Стерильность", "Атмосфера", "Цена"]
    booking = next(c for c in listed.json() if c["code"] == "booking")

    assert (
        await client.post("/api/v1/categories", json={"name": "Х"}, headers=bearer(team.manager))
    ).status_code == 403

    created = await client.post(
        "/api/v1/categories", json={"name": "Парковка"}, headers=bearer(team.owner)
    )
    assert created.status_code == 201
    assert created.json()["code"] is None
    assert created.json()["sort_order"] == 6

    duplicate = await client.post(
        "/api/v1/categories", json={"name": "Парковка"}, headers=bearer(team.owner)
    )
    assert duplicate.status_code == 409

    renamed = await client.patch(
        f"/api/v1/categories/{booking['id']}",
        json={"name": "Онлайн-запись"},
        headers=bearer(team.owner),
    )
    assert renamed.json()["name"] == "Онлайн-запись"
    assert renamed.json()["code"] == "booking"  # stable code survives renaming

    archived = await client.delete(
        f"/api/v1/categories/{booking['id']}", headers=bearer(team.owner)
    )
    assert archived.json()["archived_at"] is not None
    active = await client.get("/api/v1/categories", headers=bearer(team.owner))
    assert "Онлайн-запись" not in {c["name"] for c in active.json()}

    # A new active category may reuse the archived name; restoring the old one then conflicts.
    await client.post(
        "/api/v1/categories", json={"name": "Онлайн-запись"}, headers=bearer(team.owner)
    )
    conflict = await client.post(
        f"/api/v1/categories/{booking['id']}/restore", headers=bearer(team.owner)
    )
    assert conflict.status_code == 409


async def test_category_of_another_org_is_invisible(
    client: httpx.AsyncClient, team: Team, session: AsyncSession
) -> None:
    listed = await client.get("/api/v1/categories", headers=bearer(team.owner))
    category_id = listed.json()[0]["id"]
    intruder = await owner_token(client, "intruder@example.com")
    response = await client.patch(
        f"/api/v1/categories/{category_id}", json={"name": "Взлом"}, headers=bearer(intruder)
    )
    assert response.status_code == 404
