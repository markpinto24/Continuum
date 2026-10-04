"""HTTP middleware.

`RequestContextMiddleware` is what makes the logs greppable: it mints (or
accepts) a request id, binds it plus method/path into the structlog contextvar
context, and emits a single `http.request` line per request with duration. Every
log written downstream — extraction, resolver verdicts, Qdrant writes — inherits
that id for free.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from continuum.core import logger as clog
from continuum.core.logger import get_logger

log = get_logger("continuum.http")

REQUEST_ID_HEADER = "x-request-id"

# Endpoints we do not want a log line for on every poll.
_QUIET_PATHS = frozenset({"/", "/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

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
