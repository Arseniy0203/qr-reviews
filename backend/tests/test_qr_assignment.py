"""QR points CRUD and employee (re)assignment history."""

import httpx

from tests.api_helpers import bearer, owner_token
from tests.factories import Team


async def assign(
    client: httpx.AsyncClient, token: str, point_id: object, employee_id: object | None
) -> httpx.Response:
    return await client.put(
        f"/api/v1/qr-points/{point_id}/assignment",
        json={"employee_id": str(employee_id) if employee_id else None},
        headers=bearer(token),
    )


async def history(client: httpx.AsyncClient, token: str, point_id: object) -> list[dict]:
    response = await client.get(f"/api/v1/qr-points/{point_id}/assignments", headers=bearer(token))
    assert response.status_code == 200
    return response.json()


async def test_create_point_with_employee(client: httpx.AsyncClient, team: Team) -> None:
    response = await client.post(
        "/api/v1/qr-points",
        json={
            "branch_id": str(team.tenant.branch.id),
            "name": "Визитка — Мастер",
            "placement": "business_card",
            "employee_id": str(team.tenant.employee.id),
        },
        headers=bearer(team.owner),
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["token"]) == 10
    assert body["url"] == f"http://localhost:8080/r/{body['token']}"
    assert body["branch_name"] == "Лаванда: филиал"
    assert body["current_employee"]["id"] == str(team.tenant.employee.id)
    assert len(await history(client, team.owner, body["id"])) == 1


async def test_reassignment_closes_old_row_and_opens_new(
    client: httpx.AsyncClient, team: Team
) -> None:
    point = team.tenant.point.id
    first = await assign(client, team.owner, point, team.tenant.employee.id)
    assert first.json()["current_employee"]["id"] == str(team.tenant.employee.id)

    # Same employee again: no-op, no duplicate history row.
    await assign(client, team.owner, point, team.tenant.employee.id)
    assert len(await history(client, team.owner, point)) == 1

    # Owner may assign a master from another branch (deliberate decision).
    second = await assign(client, team.owner, point, team.employee_b.id)
    assert second.status_code == 200
    assert second.json()["current_employee"]["id"] == str(team.employee_b.id)
    assert second.json()["branch_id"] == str(team.tenant.branch.id)  # point keeps its branch

    rows = await history(client, team.owner, point)
    assert [r["employee"]["id"] for r in rows] == [
        str(team.employee_b.id),
        str(team.tenant.employee.id),
    ]
    newest, oldest = rows
    assert newest["ended_at"] is None
    assert oldest["ended_at"] is not None
    assert oldest["ended_at"] == newest["started_at"]

    unassigned = await assign(client, team.owner, point, None)
    assert unassigned.json()["current_employee"] is None
    rows = await history(client, team.owner, point)
    assert all(r["ended_at"] is not None for r in rows)
    assert len(rows) == 2


async def test_manager_limited_to_own_branch(client: httpx.AsyncClient, team: Team) -> None:
    # Cannot create a point in another branch...
    foreign = await client.post(
        "/api/v1/qr-points",
        json={"branch_id": str(team.branch_b.id), "name": "Чужая", "placement": "table"},
        headers=bearer(team.manager),
    )
    assert foreign.status_code == 404
    # ...nor see, edit, assign or download its points...
    for method, url in [
        ("GET", f"/api/v1/qr-points/{team.point_b.id}"),
        ("PATCH", f"/api/v1/qr-points/{team.point_b.id}"),
        ("DELETE", f"/api/v1/qr-points/{team.point_b.id}"),
        ("GET", f"/api/v1/qr-points/{team.point_b.id}/assignments"),
        ("GET", f"/api/v1/qr-points/{team.point_b.id}/download"),
    ]:
        response = await client.request(
            method,
            url,
            json={"name": "x"} if method == "PATCH" else None,
            headers=bearer(team.manager),
        )
        assert response.status_code == 404, (method, url)
    # ...nor assign a master of another branch to an own point.
    response = await assign(client, team.manager, team.tenant.point.id, team.employee_b.id)
    assert response.status_code == 404

    listed = await client.get("/api/v1/qr-points", headers=bearer(team.manager))
    assert [p["id"] for p in listed.json()] == [str(team.tenant.point.id)]
    own = await assign(client, team.manager, team.tenant.point.id, team.tenant.employee.id)
    assert own.status_code == 200


async def test_employee_role_has_no_qr_access(client: httpx.AsyncClient, team: Team) -> None:
    assert (await client.get("/api/v1/qr-points", headers=bearer(team.employee))).status_code == 403


async def test_archived_point_and_archived_employee_cannot_be_assigned(
    client: httpx.AsyncClient, team: Team
) -> None:
    await client.delete(f"/api/v1/employees/{team.employee_b.id}", headers=bearer(team.owner))
    response = await assign(client, team.owner, team.tenant.point.id, team.employee_b.id)
    assert response.status_code == 400

    await client.delete(f"/api/v1/qr-points/{team.tenant.point.id}", headers=bearer(team.owner))
    response = await assign(client, team.owner, team.tenant.point.id, team.tenant.employee.id)
    assert response.status_code == 400

    restored = await client.post(
        f"/api/v1/qr-points/{team.tenant.point.id}/restore", headers=bearer(team.owner)
    )
    assert restored.json()["archived_at"] is None


async def test_update_point_and_pause(client: httpx.AsyncClient, team: Team) -> None:
    response = await client.patch(
        f"/api/v1/qr-points/{team.tenant.point.id}",
        json={"name": "Стойка ресепшн", "placement": "other", "is_active": False},
        headers=bearer(team.manager),
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert response.json()["placement"] == "other"


async def test_points_of_another_org_are_invisible(client: httpx.AsyncClient, team: Team) -> None:
    intruder = await owner_token(client, "intruder@example.com")
    assert (
        await client.get(f"/api/v1/qr-points/{team.tenant.point.id}", headers=bearer(intruder))
    ).status_code == 404
    response = await assign(client, intruder, team.tenant.point.id, None)
    assert response.status_code == 404
    listed = await client.get("/api/v1/qr-points", headers=bearer(intruder))
    assert listed.json() == []
