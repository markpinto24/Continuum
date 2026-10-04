"""Sign a bare test app in as a fixed user.

Route tests build a minimal FastAPI app around one router. Rather than wire a
credential store into each, they replace the principal dependency — which is
exactly the seam every route reads identity from, so what they test is still
"the route uses whoever is signed in".
"""

from __future__ import annotations

from fastapi import FastAPI

from continuum.api.deps import get_principal
from continuum.models.auth import Principal
from continuum.services.limits import SlidingWindow


def sign_in_as(app: FastAPI, user_id: str = "mark", *, is_admin: bool = False) -> Principal:
    principal = Principal(
        user_id=user_id, email=f"{user_id}@example.com", is_admin=is_admin, via="session"
    )
    app.dependency_overrides[get_principal] = lambda: principal
    app.state.llm_limiter = SlidingWindow(1000, 60)
    return principal
