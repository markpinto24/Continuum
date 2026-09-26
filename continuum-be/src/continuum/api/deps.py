"""FastAPI dependencies.

Clients are created once at startup and stashed on `app.state`, so every request
reuses the same connection pools.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from continuum.clients.authdb import AuthDB
from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.config import Settings, get_settings
from continuum.core import logger as clog
from continuum.models.auth import Principal
from continuum.services.auth import AuthService
from continuum.services.chat import ChatService
from continuum.services.decay import DecayService
from continuum.services.extraction import FactExtractor
from continuum.services.ingest import IngestService
from continuum.services.limits import SlidingWindow
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import ResolutionService
from continuum.services.retrieval import RetrievalService
from continuum.services.speech import SpeechService


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


def get_qdrant(request: Request) -> QdrantStore:
    return request.app.state.qdrant


def get_memory_store(request: Request) -> MemoryStore:
    return request.app.state.memories


def get_extractor(request: Request) -> FactExtractor:
    return request.app.state.extractor


def get_ingest_service(request: Request) -> IngestService:
    return request.app.state.ingest


def get_resolver(request: Request) -> ResolutionService:
    return request.app.state.resolver


def get_decay_service(request: Request) -> DecayService:
    return request.app.state.decay


def get_retrieval_service(request: Request) -> RetrievalService:
    return request.app.state.retrieval


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth


def get_authdb(request: Request) -> AuthDB:
    return request.app.state.authdb


def get_speech_service(request: Request) -> SpeechService:
    return request.app.state.speech


SettingsDep = Annotated[Settings, Depends(get_settings)]
LLMDep = Annotated[LLMClient, Depends(get_llm)]
QdrantDep = Annotated[QdrantStore, Depends(get_qdrant)]
MemoryStoreDep = Annotated[MemoryStore, Depends(get_memory_store)]
ExtractorDep = Annotated[FactExtractor, Depends(get_extractor)]
IngestDep = Annotated[IngestService, Depends(get_ingest_service)]
ResolverDep = Annotated[ResolutionService, Depends(get_resolver)]
DecayDep = Annotated[DecayService, Depends(get_decay_service)]
RetrievalDep = Annotated[RetrievalService, Depends(get_retrieval_service)]
ChatDep = Annotated[ChatService, Depends(get_chat_service)]
AuthDep = Annotated[AuthService, Depends(get_auth_service)]
AuthDBDep = Annotated[AuthDB, Depends(get_authdb)]
SpeechDep = Annotated[SpeechService, Depends(get_speech_service)]


# --- Authentication ------------------------------------------------------------

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_browser_header(request: Request, settings: SettingsDep) -> None:
    """CSRF defence for requests a browser authenticates with a cookie.

    The session cookie is SameSite=Strict, which already stops a cross-site page
    from sending it. This is the second, independent layer: a custom header
    cannot be attached cross-site without a CORS preflight the policy refuses.
    Also applied to sign-in itself, so a hostile page cannot sign a visitor into
    the attacker's account and harvest what they then type into chat.
    """
    if request.method in _SAFE_METHODS:
        return
    if not request.headers.get(settings.csrf_header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing the {settings.csrf_header} header required for browser requests.",
        )


async def get_principal(
    request: Request, auth: AuthDep, settings: SettingsDep
) -> Principal:
    """Who is calling. The ONLY source of a memory owner id in the API.

    An agent sends `Authorization: Bearer ck_...`; the web UI sends the session
    cookie. A request carrying both is judged by the header alone, so a stale
    cookie in a browser can never quietly change whose graph a key writes to.
    """
    header = request.headers.get("authorization")
    if header is not None:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthorized("Expected 'Authorization: Bearer <api key>'.")
        principal = await auth.principal_from_api_key(token.strip())
        if principal is None:
            raise _unauthorized("Invalid or revoked API key.")
    else:
        token = request.cookies.get(settings.session_cookie_name)
        if not token:
            raise _unauthorized("Not signed in.")
        principal = await auth.principal_from_session(token)
        if principal is None:
            raise _unauthorized("Your session has expired. Sign in again.")
        require_browser_header(request, settings)

    clog.bind(user_id=principal.user_id)
    request.state.principal = principal
    return principal


CurrentUser = Annotated[Principal, Depends(get_principal)]


async def get_session_principal(principal: CurrentUser) -> Principal:
    """Account management needs a signed-in person, not an API key.

    A key handed to an agent must not be able to mint more keys, revoke the
    owner's, or change the password — a leaked key stays a leaked key, not a
    takeover.
    """
    if principal.via != "session":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account management requires signing in to the web UI; "
            "API keys cannot do this.",
        )
    return principal


SessionUser = Annotated[Principal, Depends(get_session_principal)]


async def get_admin(principal: SessionUser) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only.")
    return principal


AdminUser = Annotated[Principal, Depends(get_admin)]


async def limit_llm_requests(request: Request, principal: CurrentUser) -> None:
    """Per-user cap on endpoints that spend an LLM or embedding call."""
    limiter: SlidingWindow = request.app.state.llm_limiter
    wait = limiter.hit(principal.user_id)
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit reached. Try again in {wait}s.",
            headers={"Retry-After": str(wait)},
        )
