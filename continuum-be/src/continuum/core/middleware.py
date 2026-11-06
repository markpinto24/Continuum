"""HTTP middleware.

`RequestContextMiddleware` is what makes the logs greppable: it mints (or
accepts) a request id, binds it plus method/path into the structlog contextvar
context, and emits a single `http.request` line per request with duration. Every
log written downstream — extraction, resolver verdicts, Qdrant writes — inherits
that id for free.
"""

from __future__ import annotations

import re
import time
import uuid

from fastapi import HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from continuum.core import logger as clog
from continuum.core.logger import get_logger

log = get_logger("continuum.http")

REQUEST_ID_HEADER = "x-request-id"

# Endpoints we do not want a log line for on every poll.
_QUIET_PATHS = frozenset({"/", "/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico"})


# A client's own request id is kept only if it looks like one: it is echoed in
# a header and written into every log line of the request.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class BodyLimitMiddleware:
    """Refuses a request body over its limit before anything reads it.

    FastAPI reads a body before it resolves dependencies, so without this an
    anonymous multi-gigabyte request was held in memory (or a multipart upload
    spooled to disk) before the missing credential refused it. A declared
    Content-Length over the limit is refused at once; a chunked body is counted
    as it arrives and refused the moment it passes the limit.
    """

    def __init__(self, app: ASGIApp, *, default: int, overrides: dict[str, int]) -> None:
        self.app = app
        self.default = default
        self.overrides = overrides

    def _limit(self, path: str) -> int:
        for suffix, limit in self.overrides.items():
            if path.rstrip("/").endswith(suffix):
                return limit
        return self.default

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope["path"])
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await _too_large(limit)(scope, receive, send)
            return

        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _too_large_detail(limit))
            return message

        await self.app(scope, counted, send)


def _too_large_detail(limit: int) -> str:
    return f"The request is too large (over {limit // 1024} KB)."


def _too_large(limit: int) -> JSONResponse:
    return JSONResponse(
        {"detail": _too_large_detail(limit)}, status_code=status.HTTP_413_CONTENT_TOO_LARGE
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        offered = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = offered if _REQUEST_ID.fullmatch(offered) else str(uuid.uuid4())

        clog.clear()
        clog.bind(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log.exception("http.request_failed", duration_ms=duration_ms)
            clog.clear()
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        quiet = request.url.path.rstrip("/").endswith(tuple(_QUIET_PATHS)) or (
            request.url.path in _QUIET_PATHS
        )
        if not quiet or response.status_code >= 400:
            log.info(
                "http.request",
                status=response.status_code,
                duration_ms=duration_ms,
            )

        clog.clear()
        return response
