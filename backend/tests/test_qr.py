"""QR rendering: print parameters, file formats, bulk sheets and their permissions."""

import io

import httpx
import pytest
from PIL import Image

from app.services import qr_render
from app.services.qr_service import TOKEN_ALPHABET, generate_token, short_url
from tests.api_helpers import bearer
from tests.factories import Team

LABEL = qr_render.QRLabel(
    url="https://reviews.example.ru/r/abcdefghjk",
    short_url="reviews.example.ru/r/abcdefghjk",
    caption="Лаванда на Тверской · Визитка — Анна",
)


def test_token_format() -> None:
    tokens = {generate_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) == 10 and set(t) <= set(TOKEN_ALPHABET) for t in tokens)
    assert not set("0o1li") & set(TOKEN_ALPHABET)


def test_short_url_has_no_scheme() -> None:
    assert short_url("abc") == "localhost:8080/r/abc"


def test_qr_uses_level_q_without_boost() -> None:
    qr = qr_render.make_qr(LABEL.url)
    assert qr.error == "Q"
    assert not qr.is_micro


def test_png_is_print_sized_with_caption_below() -> None:
    plain = Image.open(io.BytesIO(qr_render.render_png(LABEL, with_caption=False)))
    assert plain.width >= qr_render.MIN_PNG_SIZE
    assert plain.width == plain.height

    captioned = Image.open(io.BytesIO(qr_render.render_png(LABEL)))
    assert captioned.width == plain.width
    assert captioned.height > plain.height

    # Quiet zone (4 modules) around the code stays white in the captioned image.
    gray = captioned.convert("L")
    qr = qr_render.make_qr(LABEL.url)
    module = plain.width // qr.symbol_size(scale=1, border=qr_render.QUIET_ZONE)[0]
    quiet = qr_render.QUIET_ZONE * module
    bottom_band = gray.crop((0, plain.height - quiet, plain.width, plain.height))
    assert bottom_band.getextrema() == (255, 255)


def test_svg_contains_code_and_escaped_caption() -> None:
    svg = qr_render.render_svg(
        qr_render.QRLabel(url=LABEL.url, short_url=LABEL.short_url, caption="A & B <тест>")
    ).decode()
    assert svg.startswith("<svg")
    assert "<path" in svg
    assert "A &amp; B &lt;тест&gt;" in svg
    assert LABEL.short_url in svg
    assert "<text" not in qr_render.render_svg(LABEL, with_caption=False).decode()


@pytest.mark.parametrize(("layout", "per_page"), [("1x1", 1), ("2x3", 6), ("3x4", 12)])
def test_sheet_pagination(layout: str, per_page: int) -> None:
    pdf = qr_render.render_sheet([LABEL] * 13, layout)  # type: ignore[arg-type]
    assert pdf.startswith(b"%PDF")
    expected_pages = -(-13 // per_page)
    # reportlab writes "/Type /Page" per page and "/Type /Pages" once for the page tree.
    assert pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages") == expected_pages
    assert b"DejaVuSans" in pdf  # Cyrillic-capable font is embedded
    assert b"Helvetica" not in pdf


async def test_download_formats(client: httpx.AsyncClient, team: Team) -> None:
    point_id = team.tenant.point.id
    expected = {"png": "image/png", "svg": "image/svg+xml", "pdf": "application/pdf"}
    for fmt, media_type in expected.items():
        response = await client.get(
            f"/api/v1/qr-points/{point_id}/download?format={fmt}", headers=bearer(team.manager)
        )
        assert response.status_code == 200, fmt
        assert response.headers["content-type"].startswith(media_type)
        assert (
            response.headers["content-disposition"]
            == f'attachment; filename="qr-{team.tenant.point.token}.{fmt}"'
        )
    bad = await client.get(
        f"/api/v1/qr-points/{point_id}/download?format=gif", headers=bearer(team.owner)
    )
    assert bad.status_code == 422


async def test_print_sheet_permissions(client: httpx.AsyncClient, team: Team) -> None:
    both = [str(team.tenant.point.id), str(team.point_b.id)]

    owner = await client.post(
        "/api/v1/qr-points/print",
        json={"point_ids": both, "layout": "3x4"},
        headers=bearer(team.owner),
    )
    assert owner.status_code == 200
    assert owner.headers["content-type"] == "application/pdf"
    assert owner.content.startswith(b"%PDF")

    # A manager may print only points of their own branch.
    manager_foreign = await client.post(
        "/api/v1/qr-points/print", json={"point_ids": both}, headers=bearer(team.manager)
    )
    assert manager_foreign.status_code == 404
    manager_own = await client.post(
        "/api/v1/qr-points/print",
        json={"point_ids": [str(team.tenant.point.id)]},
        headers=bearer(team.manager),
    )
    assert manager_own.status_code == 200

    bad_layout = await client.post(
        "/api/v1/qr-points/print",
        json={"point_ids": both, "layout": "5x5"},
        headers=bearer(team.owner),
    )
    assert bad_layout.status_code == 422

    await client.delete(f"/api/v1/qr-points/{team.point_b.id}", headers=bearer(team.owner))
    archived = await client.post(
        "/api/v1/qr-points/print", json={"point_ids": both}, headers=bearer(team.owner)
    )
    assert archived.status_code == 404
