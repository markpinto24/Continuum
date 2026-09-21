"""Manual decay controls.

The sweep runs on a schedule, but having it callable on demand matters: a
`dry_run` sweep is how you see what a half-life change would do before you
commit to it.
"""

from __future__ import annotations

from fastapi import APIRouter

from continuum.api.deps import DecayDep
from continuum.models.schemas import DecaySweepRequest
from continuum.services.decay import DecayReport

router = APIRouter(prefix="/decay", tags=["decay"])


@router.post("/sweep", response_model=DecayReport)
async def sweep(request: DecaySweepRequest, decay: DecayDep) -> DecayReport:
    """Apply confidence decay for one user. Set `dry_run` to preview."""
    return await decay.sweep(user_id=request.user_id, dry_run=request.dry_run)
