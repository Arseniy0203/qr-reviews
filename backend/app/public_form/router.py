"""Public review form /r/{token}: server-rendered, works without JavaScript.

Flow: GET form -> POST review (PRG) -> [contact step, if enabled] -> thanks.
CSS and JS are inlined (one request per page); a strict CSP with a per-request nonce.
"""

import secrets
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.api.deps import SessionDep
from app.core.config import get_settings
from app.core.errors import NotFoundError, RateLimitedError
from app.core.rate_limit import client_ip, enforce_rate_limit
from app.core.security import BadSignatureError, sign_value, unsign_value
from app.models import REVIEW_TEXT_MAX_LENGTH, ConsentDocument, ConsentType
from app.schemas.review import ReviewSubmission
from app.services import review_service
from app.services.review_service import ContactFormError, PointUnavailableError, PublicPoint

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
INLINE_CSS = (BASE_DIR / "static" / "form.css").read_text(encoding="utf-8")
INLINE_JS = (BASE_DIR / "static" / "form.js").read_text(encoding="utf-8")

DEVICE_COOKIE = "qr_device"
DEVICE_COOKIE_MAX_AGE = 2 * 365 * 24 * 3600
FORM_TOKEN_TTL = timedelta(hours=2)
REVIEW_REF_TTL = timedelta(minutes=30)

router = APIRouter(prefix="/r", include_in_schema=False)


# --------------------------------------------------------------------------- helpers


def render(request: Request, template: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    settings = get_settings()
    nonce = secrets.token_urlsafe(16)
    response = templates.TemplateResponse(
        request,
        template,
        {
            "css": INLINE_CSS,
            "js": INLINE_JS,
            "nonce": nonce,
            "metrika_id": settings.yandex_metrika_id,
            **context,
        },
        status_code=status_code,
    )
    metrika = " https://mc.yandex.ru" if settings.yandex_metrika_id else ""
    response.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'{metrika}; "
        f"img-src 'self' data:{metrika}; connect-src 'self'{metrika}; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    # The point token is in the URL: never leak it to maps/analytics via Referer.
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def stub(request: Request, error: PointUnavailableError) -> HTMLResponse:
    if error.paused:
        return render(
            request,
            "stub.html",
            heading="Приём отзывов приостановлен",
            message="Попробуйте позже или передайте отзыв администратору.",
        )
    return render(
        request,
        "stub.html",
        status_code=404,
        heading="QR-код недействителен",
        message="Возможно, ссылка устарела. Спросите у администратора актуальный QR-код.",
    )


def rate_limited(request: Request) -> HTMLResponse:
    return render(
        request,
        "stub.html",
        status_code=429,
        heading="Слишком много отзывов",
        message="С этой сети только что отправили много отзывов. Попробуйте чуть позже.",
    )


async def public_point(session: SessionDep, token: str) -> PublicPoint | PointUnavailableError:
    try:
        return await review_service.load_public_point(session, token)
    except PointUnavailableError as error:
        return error


PointDep = Depends(public_point)


def ensure_device_cookie(request: Request, response: Response) -> str:
    device_id = request.cookies.get(DEVICE_COOKIE)
    if not device_id or len(device_id) > 64:
        device_id = uuid.uuid4().hex
        response.set_cookie(
            DEVICE_COOKIE,
            device_id,
            max_age=DEVICE_COOKIE_MAX_AGE,
            path="/r",
            httponly=True,
            samesite="lax",
            secure=get_settings().cookie_secure,
        )
    return device_id


async def form_rate_limit(request: Request) -> bool:
    """Generous per-IP limit: a salon's shared Wi-Fi is one IP for all its customers."""
    settings = get_settings()
    try:
        await enforce_rate_limit(
            "public_form",
            client_ip(request),
            settings.rate_limit_public_form_max,
            settings.rate_limit_public_form_window_seconds,
        )
    except RateLimitedError:
        return False
    return True


def review_ref(review_id: int, token: str) -> str:
    return sign_value("review", f"{review_id}:{token}", REVIEW_REF_TTL)


def parse_review_ref(ref: str | None, token: str) -> int | None:
    if not ref:
        return None
    try:
        review_id, ref_token = unsign_value("review", ref).split(":", 1)
    except (BadSignatureError, ValueError):
        return None
    return int(review_id) if ref_token == token and review_id.isdigit() else None


REVIEW_FIELD_ERRORS = {
    "rating": "Поставьте оценку от 1 до 5",
    "text": f"Текст слишком длинный — не больше {REVIEW_TEXT_MAX_LENGTH} символов",
}


def form_page(
    request: Request,
    public: PublicPoint,
    token: str,
    *,
    status_code: int = 200,
    values: dict[str, Any] | None = None,
    errors: dict[str, str] | None = None,
) -> HTMLResponse:
    return render(
        request,
        "form.html",
        status_code=status_code,
        public=public,
        token=token,
        form_token=sign_value("form", token, FORM_TOKEN_TTL),
        text_max=REVIEW_TEXT_MAX_LENGTH,
        values={"rating": "", "category_ids": [], "text": "", **(values or {})},
        errors=errors or {},
    )


# --------------------------------------------------------------------------- review step


@router.get("/{token}", response_class=HTMLResponse)
async def show_form(
    request: Request, token: str, public: PublicPoint | PointUnavailableError = PointDep
) -> Response:
    if isinstance(public, PointUnavailableError):
        return stub(request, public)
    response = form_page(request, public, token)
    ensure_device_cookie(request, response)
    return response


@router.post("/{token}", response_class=HTMLResponse)
async def submit_review(
    request: Request,
    token: str,
    session: SessionDep,
    public: PublicPoint | PointUnavailableError = PointDep,
) -> Response:
    if isinstance(public, PointUnavailableError):
        return stub(request, public)
    if not await form_rate_limit(request):
        return rate_limited(request)

    form = await request.form()
    if form.get("website"):  # honeypot filled: a bot; pretend success, store nothing
        await review_service.record_honeypot_hit(public)
        return RedirectResponse(f"/r/{token}/thanks", status_code=303)

    values = {
        "rating": str(form.get("rating") or ""),
        "category_ids": [str(v) for v in form.getlist("category_ids")],
        "text": str(form.get("text") or ""),
    }
    try:
        if unsign_value("form", str(form.get("form_token") or "")) != token:
            raise BadSignatureError("token mismatch")
    except BadSignatureError:
        return form_page(
            request,
            public,
            token,
            status_code=400,
            values=values,
            errors={"form": "Страница устарела. Проверьте отзыв и отправьте ещё раз."},
        )

    category_ids = []
    for raw in values["category_ids"]:
        try:
            category_ids.append(uuid.UUID(raw))
        except ValueError:
            continue
    try:
        data = ReviewSubmission(
            rating=values["rating"] or 0, category_ids=category_ids, text=values["text"]
        )
    except ValidationError as exc:
        fields = {str(error["loc"][0]) for error in exc.errors()}
        errors = {f: REVIEW_FIELD_ERRORS[f] for f in fields if f in REVIEW_FIELD_ERRORS}
        return form_page(request, public, token, status_code=400, values=values, errors=errors)

    response = RedirectResponse("", status_code=303)
    device_id = ensure_device_cookie(request, response)
    review = await review_service.submit_review(
        session, public, data, device_id=device_id, ip=client_ip(request)
    )
    step = "contact" if public.settings.contact_step_enabled else "thanks"
    response.headers["Location"] = f"/r/{token}/{step}?ref={review_ref(review.id, token)}"
    return response


# --------------------------------------------------------------------------- contact step


def contact_page(
    request: Request,
    public: PublicPoint,
    token: str,
    ref: str,
    docs: review_service.ConsentDocs,
    *,
    status_code: int = 200,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
) -> HTMLResponse:
    return render(
        request,
        "contact.html",
        status_code=status_code,
        public=public,
        token=token,
        ref=ref,
        docs=docs,
        values={"name": "", "phone": "", **(values or {})},
        errors=errors or {},
    )


@router.get("/{token}/contact", response_class=HTMLResponse)
async def show_contact(
    request: Request,
    token: str,
    session: SessionDep,
    ref: str | None = None,
    public: PublicPoint | PointUnavailableError = PointDep,
) -> Response:
    if isinstance(public, PointUnavailableError):
        return stub(request, public)
    # Incomplete PD operator details or a bad link: silently skip the contact step.
    if not public.settings.contact_step_enabled or parse_review_ref(ref, token) is None:
        return RedirectResponse(f"/r/{token}/thanks", status_code=303)
    docs = await review_service.current_consents(session, public)
    return contact_page(request, public, token, ref or "", docs)


@router.post("/{token}/contact", response_class=HTMLResponse)
async def submit_contact(
    request: Request,
    token: str,
    session: SessionDep,
    public: PublicPoint | PointUnavailableError = PointDep,
) -> Response:
    if isinstance(public, PointUnavailableError):
        return stub(request, public)
    if not await form_rate_limit(request):
        return rate_limited(request)

    form = await request.form()
    ref = str(form.get("ref") or "")
    review_id = parse_review_ref(ref, token)
    thanks = RedirectResponse(f"/r/{token}/thanks", status_code=303)
    if (
        form.get("action") == "skip"
        or review_id is None
        or not public.settings.contact_step_enabled
    ):
        return thanks

    values = {"name": str(form.get("name") or ""), "phone": str(form.get("phone") or "")}
    try:
        await review_service.attach_contact(
            session,
            public,
            review_id,
            name=values["name"],
            phone=values["phone"],
            pd_consent=form.get("pd_consent") == "1",
            pd_document_id=str(form.get("pd_document_id") or "") or None,
            marketing_consent=form.get("marketing_consent") == "1",
            marketing_document_id=str(form.get("marketing_document_id") or "") or None,
        )
    except NotFoundError:
        return thanks
    except ContactFormError as error:
        field_errors = error.extra.get("errors")
        docs = await review_service.current_consents(session, public)
        return contact_page(
            request,
            public,
            token,
            ref,
            docs,
            status_code=400,
            values=values,
            errors=field_errors if isinstance(field_errors, dict) else {"form": error.message},
        )
    return thanks


# --------------------------------------------------------------------------- thanks & consents


@router.get("/{token}/thanks", response_class=HTMLResponse)
async def thanks(
    request: Request, token: str, public: PublicPoint | PointUnavailableError = PointDep
) -> Response:
    if isinstance(public, PointUnavailableError):
        return stub(request, public)
    return render(request, "thanks.html", public=public, token=token)


CONSENT_HEADINGS = {
    ConsentType.PD: "Согласие на обработку персональных данных",
    ConsentType.MARKETING: "Согласие на получение рекламных сообщений",
}


@router.get("/{token}/consent/{kind}", response_class=HTMLResponse)
async def consent(
    request: Request,
    token: str,
    kind: ConsentType,
    session: SessionDep,
    v: uuid.UUID | None = None,
    public: PublicPoint | PointUnavailableError = PointDep,
) -> Response:
    """A consent is a separate document page; `v` pins the exact version shown in the form."""
    if isinstance(public, PointUnavailableError):
        return stub(request, public)
    document: ConsentDocument | None = None
    if v is not None:
        document = await session.get(ConsentDocument, v)
        if document and (
            document.organization_id != public.organization.id or document.type != kind
        ):
            document = None
    if document is None:
        docs = await review_service.current_consents(session, public)
        document = docs.pd if kind is ConsentType.PD else docs.marketing
    if document is None:
        return stub(request, PointUnavailableError(paused=False))
    return render(
        request, "consent.html", public=public, document=document, heading=CONSENT_HEADINGS[kind]
    )
