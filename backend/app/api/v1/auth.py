from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response, status

from app.api.deps import CurrentUser, SessionDep, login_rate_limit
from app.core.config import get_settings
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services import auth_service
from app.services.auth_service import IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def set_refresh_cookie(response: Response, tokens: IssuedTokens) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=tokens.refresh_max_age,
        path=REFRESH_COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def token_response(response: Response, tokens: IssuedTokens) -> TokenResponse:
    set_refresh_cookie(response, tokens)
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.access_expires_in)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(login_rate_limit)],
)
async def register(data: RegisterRequest, session: SessionDep, response: Response) -> TokenResponse:
    user = await auth_service.register_owner(session, data)
    return token_response(response, auth_service.issue_tokens(user.id))


@router.post("/login", dependencies=[Depends(login_rate_limit)])
async def login(data: LoginRequest, session: SessionDep, response: Response) -> TokenResponse:
    user = await auth_service.authenticate(session, data.email, data.password)
    return token_response(response, auth_service.issue_tokens(user.id))


@router.post("/refresh")
async def refresh(
    session: SessionDep, response: Response, refresh_token: RefreshCookie = None
) -> TokenResponse:
    tokens = await auth_service.rotate_refresh_token(session, refresh_token)
    return token_response(response, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, refresh_token: RefreshCookie = None) -> None:
    await auth_service.logout(refresh_token)
    settings = get_settings()
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/me")
async def me(user: CurrentUser, session: SessionDep) -> MeResponse:
    return MeResponse(
        user=UserOut.model_validate(user),
        memberships=await auth_service.list_memberships(session, user.id),
    )
