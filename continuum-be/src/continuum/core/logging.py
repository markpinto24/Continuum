"""Structured logging.

Three things this buys us over `logging.basicConfig`:

1. **Request correlation.** Every log line emitted while handling a request
   carries the same `request_id`, bound once by the middleware via contextvars.
   No threading it through call signatures.

2. **Pipeline correlation.** Services bind `source_id` / `memory_id` into the
   same context, so one ingest batch is greppable end to end — extraction, every
   resolver verdict, every write.

3. **Redaction.** Memory content is user data. Outside local development we
   truncate and fingerprint it rather than shipping verbatim personal notes to a
   log aggregator.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from typing import Any

import structlog

from continuum.config import get_settings

# Keys whose values are user content and must never be logged verbatim in prod.
SENSITIVE_KEYS = frozenset(
    {"content", "text", "transcript", "excerpt", "source_excerpt", "query", "prompt"}
)

# Substrings marking a credential. Unlike user content, these are dropped in
# EVERY environment including local — a key pasted into a dev log is still a
# leaked key, and dev logs are the ones that end up in screenshots and issues.
SECRET_KEY_MARKERS = ("api_key", "apikey", "token", "secret", "password", "authorization")

_MAX_PREVIEW = 80


def redact_secrets(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Drop credential-shaped values everywhere, always."""
    for key in list(event_dict):
        lowered = key.lower()
        if any(marker in lowered for marker in SECRET_KEY_MARKERS):
            event_dict[key] = "<redacted>"
    return event_dict


def redact_user_content(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Truncate + fingerprint user content outside local dev.

    The fingerprint keeps lines correlatable (same text -> same hash) without
    storing the text itself.
    """
    for key in list(event_dict):
        if key not in SENSITIVE_KEYS:
            continue
        value = event_dict[key]
        if not isinstance(value, str) or not value:
            continue
        digest = hashlib.sha256(value.encode()).hexdigest()[:10]
        event_dict[key] = f"<redacted len={len(value)} sha={digest}>"
    return event_dict


def truncate_user_content(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Local dev: keep content readable, but stop one long note flooding stdout."""
    for key in list(event_dict):
        if key not in SENSITIVE_KEYS:
            continue
        value = event_dict[key]
        if isinstance(value, str) and len(value) > _MAX_PREVIEW:
            event_dict[key] = value[:_MAX_PREVIEW] + f"… (+{len(value) - _MAX_PREVIEW})"
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        # Always on, every environment. User-content policy varies below;
        # credential policy does not.
        redact_secrets,
    ]

    if settings.is_local:
        shared.append(truncate_user_content)
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        shared.append(redact_user_content)
        renderer = structlog.processors.JSONRenderer()

    # structlog's own chain stops short of rendering and hands the event dict to
    # the stdlib formatter below. Rendering in both places nests the JSON inside
    # its own "event" field, so `wrap_for_formatter` must be the last processor
    # here — not the renderer.
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # One formatter renders everything — our logs and foreign ones (uvicorn,
    # httpx, qdrant_client) — so output is uniform in shape and destination.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*shared, structlog.processors.format_exc_info],
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for noisy, noisy_level in (
        ("uvicorn.access", logging.WARNING),  # our middleware logs this with timing
        ("httpx", logging.WARNING),
        ("httpcore", logging.WARNING),
        ("openai", logging.WARNING),
        ("apscheduler", logging.WARNING),
    ):
        logging.getLogger(noisy).setLevel(noisy_level)


def bind(**kwargs: Any) -> None:
    """Bind values into the current request/task log context."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear() -> None:
    structlog.contextvars.clear_contextvars()
