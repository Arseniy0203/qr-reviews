"""Typed application errors. Services raise them; one handler turns them into JSON.

Messages are user-facing, hence Russian.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400
    code = "bad_request"
    message = "Некорректный запрос"

    def __init__(self, message: str | None = None, **extra: object) -> None:
        self.message = message or self.message
        # Machine-readable details for the UI (e.g. which fields to highlight).
        self.extra = extra
        super().__init__(self.message)


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Требуется авторизация"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"
    message = "Недостаточно прав"


class NotFoundError(AppError):
    """Also used for objects outside the caller's scope, so their existence is not revealed."""

    status_code = 404
    code = "not_found"
    message = "Не найдено"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "Конфликт данных"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Слишком много запросов. Попробуйте позже."

    def __init__(self, retry_after: int, message: str | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    headers = {}
    if isinstance(exc, RateLimitedError):
        headers["Retry-After"] = str(exc.retry_after)
    if isinstance(exc, AuthenticationError):
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code, **exc.extra},
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
