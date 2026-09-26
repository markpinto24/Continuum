"""Memory-augmented chat.

The route does two things and no more: turn `ChatEvent`s into SSE frames, and
reject a request with nothing to answer. All the logic lives in `ChatService`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from continuum.api.deps import (
    ChatDep,
    CurrentUser,
    RetrievalDep,
    SettingsDep,
    limit_llm_requests,
)
from continuum.models.schemas import ChatBody, ChatContext, ChatContextRequest, ChatRequest
from continuum.services.chat import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])

# Proxies that buffer chunked responses turn a stream into one late blob; this
# is the header nginx honours to stay out of the way.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _sse(service: ChatService, request: ChatRequest) -> AsyncIterator[str]:
    async for event in service.stream(request):
        yield f"event: {event.event}\ndata: {json.dumps(event.data)}\n\n"


@router.post("", dependencies=[Depends(limit_llm_requests)])
async def chat(
    body: ChatBody, principal: CurrentUser, service: ChatDep, settings: SettingsDep
) -> StreamingResponse:
    """Answer from memory, streaming over SSE.

    Event sequence:

    - `context` — the memories retrieved for this turn, with their ranking
      broken out, plus any unresolved disagreements among them. Sent before the
      first token so the UI can show what is in play while the answer streams.
    - `delta`   — one chunk of answer text.
    - `done`    — memory ids used, ids actually cited, and what this turn
      contributed back to the graph.
    - `error`   — the stream failed; no further events follow.
    """
    if not body.latest_user_message():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="`messages` must contain at least one non-empty user message.",
        )
    if body.total_chars() > settings.max_input_chars:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Messages are over the {settings.max_input_chars}-character limit.",
        )
    request = ChatRequest(**body.model_dump(), user_id=principal.user_id)
    return StreamingResponse(
        _sse(service, request),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post(
    "/context", response_model=ChatContext, dependencies=[Depends(limit_llm_requests)]
)
async def chat_context(
    request: ChatContextRequest, principal: CurrentUser, retrieval: RetrievalDep
) -> ChatContext:
    """What chat *would* retrieve for a query, without generating an answer.

    Cheap enough to call on every keystroke in the graph UI, and the hook Phase 5
    needs to score retrieval on its own rather than through the answer.
    """
    return await retrieval.retrieve(
        user_id=principal.user_id, query=request.query, limit=request.limit
    )
