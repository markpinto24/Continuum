"""Accounts, credentials and the authenticated principal.

Pure models, no I/O — the same rule as `memory.py`.

`User.id` is the value stored as `user_id` on every memory. It is deliberately
not the email: an email changes, and re-keying a belief graph because someone
changed address would be a migration for no reason.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# A memory owner id travels in Qdrant payloads, logs and filenames — keep it to
# characters that are safe in all three.
USER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalise_email(value: str) -> str:
    return value.strip().lower()


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL.match(value))


class User(BaseModel):
    id: str
    email: str
    is_admin: bool = False
    disabled: bool = False
    created_at: datetime


class ApiKey(BaseModel):
    """A stored key. The secret itself is never kept — only its hash and a prefix
    long enough to recognise it in a list."""

    id: str
    user_id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class Principal(BaseModel):
    """Who is making this request, established from a credential — never from
    anything the request body claims."""

    user_id: str
    email: str
    is_admin: bool = False
    via: Literal["session", "api_key"]
    api_key_id: str | None = Field(
        default=None, description="Set when authenticated by an API key."
    )
