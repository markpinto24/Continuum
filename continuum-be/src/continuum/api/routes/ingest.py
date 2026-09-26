from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from continuum.api.deps import CurrentUser, IngestDep, SettingsDep, limit_llm_requests
from continuum.models.schemas import IngestBody, IngestRequest, IngestResponse

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post(
    "",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_llm_requests)],
)
async def ingest(
    body: IngestBody, principal: CurrentUser, service: IngestDep, settings: SettingsDep
) -> IngestResponse:
    """Feed the system something to remember.

    Accepts either a free-text note or a message transcript. Returns what was
    created, what was recognised as already known, and which existing memories
    sit close enough to the new ones to be worth a second look. The memories
    belong to whoever the API key or session belongs to.
    """
    transcript = body.as_transcript()
    if not transcript:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provide either 'text' or a non-empty 'messages' list.",
        )
    if len(transcript) > settings.max_input_chars:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Input is over the {settings.max_input_chars}-character limit. Split it.",
        )
    request = IngestRequest(**body.model_dump(), user_id=principal.user_id)
    return await service.ingest(request)
