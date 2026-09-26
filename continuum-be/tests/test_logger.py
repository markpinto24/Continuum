"""The logger: output shape, and the redaction it must never lose.

Each test builds its own configuration and logs through a fresh logger name, so
the result does not depend on what earlier tests configured.
"""

from __future__ import annotations

import itertools
import json
import logging
import re

import pytest

from continuum.core import logger as clog
from continuum.core.logger import Logger, LoggerConfig

_names = itertools.count()

LINE = re.compile(
    r"^(?P<level>[A-Z]+):\t   Timestamp: (?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)"
    r" \| Module: (?P<module>\S+) \| Function: (?P<func>\S+) \| Message: (?P<message>.*)$"
)


def build(**overrides: object):
    config = LoggerConfig(name="continuum", colors=False, **overrides)
    return Logger(config).get_logger(f"test.logger.{next(_names)}")


@pytest.fixture(autouse=True)
def _restore_logging():
    yield
    clog.clear()
    clog.configure_logging()


def last_line(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_line_format_matches_the_house_style(capsys):
    log = build(log_format="line")
    log.info("thing.happened", memory_id="m1")

    match = LINE.match(last_line(capsys))
    assert match, last_line(capsys)
    assert match["level"] == "INFO"
    # The caller, not the logging machinery.
    assert match["module"] == "test_logger"
    assert match["func"] == "test_line_format_matches_the_house_style"
    assert match["message"] == "thing.happened | memory_id=m1"


def test_levels_are_coloured_when_colours_are_on(capsys):
    log = Logger(LoggerConfig(name="continuum", colors=True)).get_logger("test.logger.colour")
    log.warning("careful")

    out = last_line(capsys)
    assert out.startswith("\033[93mWARNING:")
    assert out.endswith("\033[0m")


def test_json_format_is_one_parseable_object_with_the_context(capsys):
    log = build(log_format="json")
    clog.bind(request_id="req-1")
    log.info("ingest.resolved", verdict="supersedes")

    record = json.loads(last_line(capsys))
    assert record["level"] == "INFO"
    assert record["module"] == "test_logger"
    assert record["function"] == "test_json_format_is_one_parseable_object_with_the_context"
    assert record["message"] == "ingest.resolved"
    assert record["verdict"] == "supersedes"
    assert record["request_id"] == "req-1"
    assert "T" in record["timestamp"]  # ISO 8601, for machines


def test_bound_context_appears_on_every_line(capsys):
    log = build()
    clog.bind(request_id="req-42", user_id="mark")
    log.info("a")
    log.info("b")

    for line in capsys.readouterr().out.strip().splitlines()[-2:]:
        assert "request_id=req-42" in line and "user_id=mark" in line


@pytest.mark.parametrize("log_format", ["line", "json"])
def test_credentials_are_redacted_in_every_format(capsys, log_format):
    log = build(log_format=log_format, redact_user_content=False)
    log.info("auth.debug", api_key="ck_live_secret", password="hunter2")

    out = last_line(capsys)
    assert "ck_live_secret" not in out and "hunter2" not in out
    assert "<redacted>" in out


def test_user_content_is_fingerprinted_outside_local(capsys):
    log = build(redact_user_content=True)
    log.info("ingest.fact", content="Sara owns the billing service")

    out = last_line(capsys)
    assert "Sara" not in out
    assert re.search(r"content=<redacted len=29 sha=[0-9a-f]{10}>", out)


def test_user_content_is_only_truncated_locally(capsys):
    log = build(redact_user_content=False)
    log.info("ingest.fact", content="x" * 200)

    out = last_line(capsys)
    assert "x" * 80 + "… (+120)" in out


def test_foreign_loggers_get_the_same_shape(capsys):
    build()
    logging.getLogger("uvicorn.error").info("Application startup complete.")

    match = LINE.match(last_line(capsys))
    assert match, last_line(capsys)
    assert match["message"] == "Application startup complete."
    assert match["func"] == "test_foreign_loggers_get_the_same_shape"


def test_positional_arguments_are_formatted(capsys):
    log = build()
    log.info("pulled %s models", 2)

    assert last_line(capsys).endswith("Message: pulled 2 models")


def test_an_exception_keeps_its_traceback(capsys):
    log = build()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("resolution.judge_failed")

    out = capsys.readouterr().out
    assert "Message: resolution.judge_failed" in out
    assert "Traceback" in out and "RuntimeError: boom" in out


def test_debug_is_filtered_at_info(capsys):
    log = build(level="INFO")
    log.debug("noise")
    log.info("signal")

    out = capsys.readouterr().out
    assert "noise" not in out and "signal" in out


def test_get_logger_configures_itself(monkeypatch, capsys):
    monkeypatch.setattr(clog, "_logger_instance", None)
    clog.get_logger("test.logger.lazy").info("first.use")

    assert "Message: first.use" in capsys.readouterr().out
