"""User management. Admins only, and only from a signed-in session."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from continuum.api.deps import AdminUser, AuthDep
from continuum.api.routes.auth import http_error
from continuum.models.auth import User
from continuum.models.schemas import (
    UserCreateRequest,
    UserListResponse,
    UserSummary,
    UserUpdateRequest,
)
from continuum.services.auth import AuthError

router = APIRouter(prefix="/admin", tags=["admin"])


def _summary(user: User) -> UserSummary:
    return UserSummary.model_validate(user.model_dump())


@router.get("/users", response_model=UserListResponse)
async def list_users(admin: AdminUser, auth: AuthDep) -> UserListResponse:
    return UserListResponse(users=[_summary(u) for u in await auth.list_users()])


@router.post("/users", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreateRequest, admin: AdminUser, auth: AuthDep) -> UserSummary:
    """Create an account. Leave `user_id` empty unless adopting existing memories."""
    try:
        user = await auth.create_user(
            body.email, body.password, user_id=body.user_id, is_admin=body.is_admin
        )
    except AuthError as exc:
        raise http_error(exc) from exc
    return _summary(user)


@router.patch("/users/{user_id}", response_model=UserSummary)
async def update_user(
    user_id: str, body: UserUpdateRequest, admin: AdminUser, auth: AuthDep
) -> UserSummary:
    """Disable or re-enable an account, or change whether it is an admin.

    Disabling signs the user out everywhere and stops their API keys working. It
    deletes nothing: their memories stay exactly as they were.
    """
    if user_id == admin.user_id and body.disabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="You cannot disable your own account.",
        )
    try:
        user = await auth.set_user_state(user_id, disabled=body.disabled, is_admin=body.is_admin)
    except AuthError as exc:
        raise http_error(exc) from exc
    return _summary(user)
