"""Logger module.

Usage:
    from continuum.core.logger import get_logger
    log = get_logger(__name__)          # or get_logger() for the app logger
    log.info("ingest.resolved", memory_id=memory.id, verdict="supersedes")

Output, in the default "line" format (coloured by level):

    INFO:	   Timestamp: 2026-09-26 15:40:01 | Module: ingest | Function: ingest
               | Message: ingest.resolved | memory_id=… verdict=supersedes
    (one line in practice; wrapped here)

or one JSON object per line with LOG_FORMAT=json (level, timestamp, module,
function, message, then every context field) — the shape a log aggregator wants.

Under the formatters sits structlog, for three things plain `logging` cannot do:

1. **Context without threading it through calls.** The middleware binds
   `request_id` once; the auth dependency binds `user_id`; ingest binds
   `source_id`. Every line emitted while handling that request carries them.
2. **Key-value fields.** `log.info("event", key=value)` — greppable and
   machine-readable, where an f-string buries values in prose.
3. **Redaction.** Memory content is user data: outside local development it is
   fingerprinted, never written verbatim. Credential-shaped fields are dropped
   in EVERY environment.

Foreign loggers — uvicorn, alembic, httpx, qdrant_client — go through the same
formatter, so every line on stdout has one shape.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import sys
from typing import Any, Literal

import structlog
from pydantic import BaseModel
from structlog.processors import CallsiteParameter, CallsiteParameterAdder
from structlog.typing import EventDict, Processor

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

# Rendered in their own slots, not repeated among the trailing key=value fields.
_RESERVED = ("event", "level", "timestamp", "module", "func_name", "logger", "exception")


class LoggerConfig(BaseModel):
    """Logger configuration"""

    name: str
    level: str = "INFO"
    log_handlers: list[str] = ["stream"]
    log_format: Literal["line", "json"] = "line"
    colors: bool = True
    # Outside local development, user content is fingerprinted instead of
    # truncated. Credentials are dropped regardless of this.
    redact_user_content: bool = True


# --- Redaction processors -----------------------------------------------------


def redact_secrets(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """Drop credential-shaped values everywhere, always."""
    for key in list(event_dict):
        lowered = key.lower()
        if any(marker in lowered for marker in SECRET_KEY_MARKERS):
            event_dict[key] = "<redacted>"
    return event_dict


def redact_user_content(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
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


def truncate_user_content(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """Local dev: keep content readable, but stop one long note flooding stdout."""
    for key in list(event_dict):
        if key not in SENSITIVE_KEYS:
            continue
        value = event_dict[key]
        if isinstance(value, str) and len(value) > _MAX_PREVIEW:
            event_dict[key] = value[:_MAX_PREVIEW] + f"… (+{len(value) - _MAX_PREVIEW})"
    return event_dict


# --- Formatters -----------------------------------------------------------------


class _EventFormatter(structlog.stdlib.ProcessorFormatter):
    """A logging.Formatter that renders structlog events AND plain stdlib records.

    Subclasses implement `render(event_dict) -> str`. Everything before it —
    context, call site, redaction — is the shared chain, so a uvicorn line and
    one of ours are processed identically.
    """

    def __init__(self, pre_chain: list[Processor]) -> None:
        super().__init__(
            foreign_pre_chain=pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                self._render,
            ],
        )

    def _render(self, _logger: Any, _method: str, event_dict: EventDict) -> str:
        return self.render(event_dict)

    def render(self, event_dict: EventDict) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


class LineFormatter(_EventFormatter):
    """Liner Formatter"""

    COLORS = {
        "DEBUG": "\033[38;2;41;153;153m",  # Blue
        "INFO": "\033[38;2;171;192;35m",  # Green
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[38;2;204;102;110m",  # Red
        "CRITICAL": "\033[38;2;204;102;110m",  # Red
    }

    def __init__(self, pre_chain: list[Processor], *, colors: bool = True) -> None:
        super().__init__(pre_chain)
        self.colors = colors

    def render(self, event_dict: EventDict) -> str:
        level = str(event_dict.get("level", "info")).upper()
        if level == "WARN":
            level = "WARNING"
        color_start = self.COLORS.get(level, "") if self.colors else ""
        color_end = "\033[0m" if color_start else ""

        fields = " ".join(
            f"{key}={value}" for key, value in event_dict.items() if key not in _RESERVED
        )
        message = str(event_dict.get("event", ""))
        if fields:
            message = f"{message} | {fields}"

        line = (
            f"{color_start}{level}:\t   Timestamp: {_local_time(event_dict.get('timestamp'))}"
            f" | Module: {event_dict.get('module', '-')}"
            f" | Function: {event_dict.get('func_name', '-')}"
            f" | Message: {message}{color_end}"
        )
        if exception := event_dict.get("exception"):
            line = f"{line}\n{exception}"
        return line


class JsonFormatter(_EventFormatter):
    """JSON Formatter"""

    @staticmethod
    def json_encoder(obj: Any) -> str:
        """Json encoding for logs"""
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        return str(obj)

    def render(self, event_dict: EventDict) -> str:
        """convert for json format and dumps"""
        log_data: dict[str, Any] = {
            "level": str(event_dict.get("level", "info")).upper(),
            "timestamp": event_dict.get("timestamp"),
            "module": event_dict.get("module"),
            "function": event_dict.get("func_name"),
            "message": event_dict.get("event", ""),
        }
        # Context fields (request_id, user_id, memory_id, …) stay top-level:
        # they are what makes a JSON log worth querying.
        log_data.update(
            {key: value for key, value in event_dict.items() if key not in _RESERVED}
        )
        if exception := event_dict.get("exception"):
            log_data["exception"] = exception
        return json.dumps(log_data, default=self.json_encoder)


def _local_time(iso: object) -> str:
    """The line format is for people: local wall-clock time, to the second."""
    if not isinstance(iso, str):
        return "-"
    try:
        return datetime.datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is at the moment of each record.

    A plain StreamHandler captures the stream object once. pytest swaps
    sys.stdout during a run and closes its replacement afterwards, and a handler
    still holding the old object then fails with "I/O operation on closed file".
    """

    @property  # type: ignore[override]
    def stream(self) -> Any:
        return sys.stdout

    @stream.setter
    def stream(self, _value: Any) -> None:
        pass


# --- Configuration ----------------------------------------------------------------


class Logger:
    """Logger object"""

    def __init__(self, config: LoggerConfig) -> None:
        self.config = config
        level = getattr(logging, config.level.upper(), logging.INFO)

        pre_chain: list[Processor] = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Where the call was made — for foreign records too, read from the
            # LogRecord — so "Module | Function" is the caller, not this file.
            CallsiteParameterAdder([CallsiteParameter.MODULE, CallsiteParameter.FUNC_NAME]),
            structlog.stdlib.PositionalArgumentsFormatter(),  # log.info("x %s", y) works too
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
            # Always on, every environment. User-content policy varies below;
            # credential policy does not.
            redact_secrets,
            redact_user_content if config.redact_user_content else truncate_user_content,
            structlog.processors.format_exc_info,
        ]

        # structlog's own chain stops short of rendering and hands the event dict
        # to the stdlib formatter. `wrap_for_formatter` must be last here: render
        # in both places and the output nests inside its own "event" field.
        structlog.configure(
            processors=[*pre_chain, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

        if config.log_format == "line":
            formatter: logging.Formatter = LineFormatter(pre_chain, colors=config.colors)
        elif config.log_format == "json":
            formatter = JsonFormatter(pre_chain)
        else:  # pragma: no cover - pydantic already restricts the value
            raise ValueError("Invalid log_format specified")

        handlers: list[logging.Handler] = []
        if "stream" in config.log_handlers:
            handler = _StdoutHandler()
            handler.setFormatter(formatter)
            handlers.append(handler)

        # One handler, on the root. Every logger — ours and foreign — propagates
        # to it, so nothing is printed twice and nothing escapes the redaction.
        root = logging.getLogger()
        root.handlers = handlers
        root.setLevel(level)

        # uvicorn installs its own handlers and formatters; hand its records to
        # the root instead, or its lines keep a different shape from ours.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            foreign = logging.getLogger(name)
            foreign.handlers = []
            foreign.propagate = True

        for noisy, noisy_level in (
            ("uvicorn.access", logging.WARNING),  # our middleware logs this with timing
            ("httpx", logging.WARNING),
            # A separately named httpx a dependency ships; without this every
            # LLM call — each health check included — logs an INFO line.
            ("httpx2", logging.WARNING),
            ("httpcore", logging.WARNING),
            ("openai", logging.WARNING),
            ("apscheduler", logging.WARNING),
            ("aiosqlite", logging.WARNING),
            # "setup plugin …" on every start. alembic.runtime.migration stays at
            # INFO: "Running upgrade 0001 -> 0002" is worth seeing.
            ("alembic.runtime.plugins", logging.WARNING),
        ):
            logging.getLogger(noisy).setLevel(noisy_level)

    def get_logger(self, name: str | None = None) -> structlog.stdlib.BoundLogger:
        """Getter of logger object"""
        return structlog.get_logger(name or self.config.name)


# Factory function to create logger config with settings


def get_logger_config() -> LoggerConfig:
    settings = get_settings()
    return LoggerConfig(
        name=settings.app_name.lower(),
        level=settings.log_level,
        log_format=settings.log_format,
        colors=settings.log_colors,
        redact_user_content=not settings.is_local,
    )


_logger_instance: Logger | None = None


def configure_logging() -> Logger:
    """(Re)build logging from settings. Called at startup; safe to call again."""
    global _logger_instance
    _logger_instance = Logger(config=get_logger_config())
    return _logger_instance


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """The logger for `name` (a module's `__name__`), or the app logger.

    Configures logging from settings on first use, so a module can call it at
    import time and a script needs no setup. `configure_logging()` rebuilds it
    (the API does, at startup) if settings changed in between.
    """
    if _logger_instance is None:
        configure_logging()
    assert _logger_instance is not None
    return _logger_instance.get_logger(name)


def bind(**kwargs: Any) -> None:
    """Bind values into the current request/task log context."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear() -> None:
    structlog.contextvars.clear_contextvars()
