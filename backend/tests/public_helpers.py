"""Helpers for driving the public form like a browser would."""

import re
import uuid
from collections.abc import Sequence

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import Employee, Organization, QRAssignment
from app.schemas.organization import OrgSettings

FORM_TOKEN = re.compile(r'name="form_token" value="([^"]+)"')
LEGAL = {
    "legal_name": "ООО «Лаванда»",
    "inn": "7701234567",
    "legal_address": "Москва, ул. Тверская, 1",
    "privacy_email": "privacy@lavanda.ru",
}


def client_from(ip: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 51000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def open_form(client: httpx.AsyncClient, token: str) -> tuple[str, str]:
    """GET the form like a browser: returns (form_token, device cookie)."""
    client.cookies.clear()
    response = await client.get(f"/r/{token}")
    assert response.status_code == 200, response.text
    match = FORM_TOKEN.search(response.text)
    assert match
    return match.group(1), response.cookies["qr_device"]


async def post_review(
    client: httpx.AsyncClient,
    token: str,
    *,
    rating: int | str = 5,
    text: str = "",
    category_ids: Sequence[object] = (),
    device: str | None = None,
    form_token: str | None = None,
    extra: dict[str, str] | None = None,
) -> httpx.Response:
    if form_token is None or device is None:
        fresh_token, fresh_device = await open_form(client, token)
        form_token = form_token or fresh_token
        device = device or fresh_device
    client.cookies.clear()
    data: dict[str, object] = {
        "form_token": form_token,
        "rating": str(rating),
        "text": text,
        "category_ids": [str(c) for c in category_ids],
        **(extra or {}),
    }
    return await client.post(f"/r/{token}", data=data, headers={"Cookie": f"qr_device={device}"})


def location_ref(response: httpx.Response) -> str:
    return response.headers["location"].split("ref=", 1)[1]


async def set_org_settings(
    session: AsyncSession, organization_id: uuid.UUID, **changes: object
) -> None:
    organization = await session.get(Organization, organization_id)
    assert organization is not None
    current = OrgSettings.from_db(organization.settings).model_dump()
    organization.settings = OrgSettings.model_validate({**current, **changes}).to_db()
    await session.commit()


async def assign(session: AsyncSession, employee: Employee, point_id: uuid.UUID) -> None:
    session.add(
        QRAssignment(
            organization_id=employee.organization_id,
            qr_point_id=point_id,
            employee_id=employee.id,
        )
    )
    await session.commit()
