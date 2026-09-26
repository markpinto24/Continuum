"""Sign-in, sessions and API keys.

`/status`, `/setup` and `/login` are the only endpoints in the API reachable
without a credential. Everything under `/keys` and `/password` needs a signed-in
person: an API key can call the memory API but can never manage credentials.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from continuum.api.deps import (
    AuthDep,
    CurrentUser,
    SessionUser,
    SettingsDep,
    require_browser_header,
)
from continuum.config import Settings
from continuum.models.auth import ApiKey, Principal, User
from continuum.models.schemas import (
    ApiKeyCreated,
    ApiKeyCreateRequest,
    ApiKeyListResponse,
    ApiKeySummary,
    AuthStatus,
    LoginRequest,
    Me,
    PasswordChangeRequest,
    SetupRequest,
)
from continuum.services.auth import AuthError, LockedOut

router = APIRouter(prefix="/auth", tags=["auth"])


def http_error(exc: AuthError) -> HTTPException:
    headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, LockedOut) else None
    return HTTPException(status_code=exc.status, detail=str(exc), headers=headers)


def client_address(request: Request, settings: Settings) -> str | None:
    if settings.trust_proxy_headers and (real := request.headers.get("x-real-ip")):
        return real.strip()
    return request.client.host if request.client else None


def _cookie_secure(request: Request, settings: Settings) -> bool:
    if settings.session_cookie_secure is not None:
        return settings.session_cookie_secure
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"


def _set_session_cookie(
    response: Response, request: Request, settings: Settings, token: str, expires: datetime
) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        expires=expires,
        httponly=True,  # page scripts never see it, so an XSS cannot exfiltrate it
        secure=_cookie_secure(request, settings),
        # Strict: never sent on a request another site starts. The SPA only ever
        # calls its own origin, so nothing legitimate needs it cross-site.
        samesite="strict",
        path="/",
    )


def _me(user: User | Principal, via: str = "session") -> Me:
    if isinstance(user, Principal):
        return Me(user_id=user.user_id, email=user.email, is_admin=user.is_admin, via=user.via)
    return Me(user_id=user.id, email=user.email, is_admin=user.is_admin, via=via)


def _summary(key: ApiKey) -> ApiKeySummary:
    return ApiKeySummary.model_validate(key.model_dump())


@router.get("/status", response_model=AuthStatus)
async def auth_status(auth: AuthDep, settings: SettingsDep) -> AuthStatus:
    """Whether the instance still needs its first admin. No credential required."""
    return AuthStatus(
        needs_setup=await auth.needs_setup(),
        web_setup_allowed=settings.auth_allow_web_setup,
    )


@router.post(
    "/setup",
    response_model=Me,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_browser_header)],
)
async def setup(
    body: SetupRequest,
    request: Request,
    response: Response,
    auth: AuthDep,
    settings: SettingsDep,
) -> Me:
    """Create the first admin and sign them in. Refused once any account exists."""
    try:
        user = await auth.setup_first_admin(body.email, body.password, body.user_id)
    except AuthError as exc:
        raise http_error(exc) from exc
    token, expires = await auth.create_session(user)
    _set_session_cookie(response, request, settings, token, expires)
    return _me(user)


@router.post("/login", response_model=Me, dependencies=[Depends(require_browser_header)])
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth: AuthDep,
    settings: SettingsDep,
) -> Me:
    """Sign in to the web UI. Agents use API keys instead."""
    try:
        user = await auth.authenticate_password(
            body.email, body.password, client_address(request, settings)
        )
    except AuthError as exc:
        raise http_error(exc) from exc
    token, expires = await auth.create_session(user)
    _set_session_cookie(response, request, settings, token, expires)
    return _me(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_browser_header)],
)
async def logout(request: Request, auth: AuthDep, settings: SettingsDep) -> Response:
    """End this browser's session. Succeeds even if it had already expired."""
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await auth.end_session(token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=_cookie_secure(request, settings),
        samesite="strict",  # same attributes as when it was set
    )
    return response


@router.get("/me", response_model=Me)
async def me(principal: CurrentUser) -> Me:
    """Who this credential belongs to. Handy for checking an agent's key works."""
    return _me(principal)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    principal: SessionUser,
    request: Request,
    auth: AuthDep,
    settings: SettingsDep,
) -> Response:
    """Change your password. Signs out every other browser."""
    try:
        await auth.change_password(
            principal.user_id,
            body.current_password,
            body.new_password,
            current_session=request.cookies.get(settings.session_cookie_name),
        )
    except AuthError as exc:
        raise http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/keys", response_model=ApiKeyListResponse)
async def list_keys(principal: SessionUser, auth: AuthDep) -> ApiKeyListResponse:
    return ApiKeyListResponse(
        keys=[_summary(k) for k in await auth.list_api_keys(principal.user_id)]
    )


@router.post("/keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: ApiKeyCreateRequest, principal: SessionUser, auth: AuthDep
) -> ApiKeyCreated:
    """Mint an API key for an agent. The full key is in this response and nowhere else."""
    try:
        key, secret = await auth.create_api_key(principal.user_id, body.name)
    except AuthError as exc:
        raise http_error(exc) from exc
    return ApiKeyCreated(key=_summary(key), secret=secret)


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: str, principal: SessionUser, auth: AuthDep) -> Response:
    """Revoke a key immediately. It stays listed, marked revoked, for the record."""
    try:
        await auth.revoke_api_key(principal.user_id, key_id)
    except AuthError as exc:
        raise http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
