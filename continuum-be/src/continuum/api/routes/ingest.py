from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from continuum.api.deps import IngestDep
from continuum.models.schemas import IngestRequest, IngestResponse

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest(request: IngestRequest, service: IngestDep) -> IngestResponse:
    """Feed the system something to remember.

    Accepts either a free-text note or a message transcript. Returns what was
    created, what was recognised as already known, and which existing memories
    sit close enough to the new ones to be worth a second look.
    """
    if not request.as_transcript():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either 'text' or a non-empty 'messages' list.",
        )
    return await service.ingest(request)
