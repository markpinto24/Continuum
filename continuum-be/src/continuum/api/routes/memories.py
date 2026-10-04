from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from continuum.api.deps import (
    CorrectionDep,
    CurrentUser,
    MemoryStoreDep,
    SettingsDep,
    SharingDep,
    SummaryDep,
    limit_llm_requests,
)
from continuum.models.auth import Principal
from continuum.models.memory import (
    SHARED_SPACE,
    Memory,
    MemoryCategory,
    MemoryStatus,
    visible_owners,
)
from continuum.models.schemas import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    MemoryExport,
    MemoryListResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    RejectBody,
    RejectResponse,
    ScoredMemory,
    ShareResponse,
    SummaryRefreshResponse,
)
from continuum.services.corrections import CorrectionError
from continuum.services.sharing import SharingError

router = APIRouter(prefix="/memories", tags=["memories"])


async def _owned(memories: MemoryStoreDep, memory_id: str, principal: Principal) -> Memory:
    """Fetch a memory the caller owns.

    Someone else's memory answers 404, not 403: a different status would confirm
    that the id exists, which is itself a leak when ids appear in shared logs.
    """
    memory = await memories.get(memory_id)
    # Your own memories, and the shared space's — every member may read and
    # confirm team knowledge. Anyone else's private memory does not exist here.
    if not memory or memory.user_id not in (principal.user_id, SHARED_SPACE):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return memory


def _may_correct(memory: Memory, principal: Principal) -> None:
    """Your own memories; a shared one only if you shared it, or you are an admin.

    Reading and confirming team knowledge is every member's; retiring or
    erasing it is not.
    """
    if memory.user_id == principal.user_id:
        return
    if memory.is_shared and (principal.is_admin or memory.shared_by == principal.user_id):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail="Only whoever shared this memory, or an admin, can change it.",
    )


@router.get("/export", response_model=MemoryExport)
async def export_memories(principal: CurrentUser, memories: MemoryStoreDep) -> JSONResponse:
    """Download your whole graph — every status, every edge — as JSON.

    Your own memories only; the team space belongs to everyone (an admin backs it
    up with scripts/backup.py).
    """
    items = await memories.list_all(user_id=principal.user_id, limit=100_000)
    export = MemoryExport(
        exported_at=datetime.now(UTC), user_id=principal.user_id, total=len(items),
        memories=items,
    )
    stamp = export.exported_at.strftime("%Y%m%d")
    return JSONResponse(
        export.model_dump(mode="json"),
        headers={
            "Content-Disposition": f'attachment; filename="continuum-memories-{stamp}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/summaries/refresh",
    response_model=SummaryRefreshResponse,
    dependencies=[Depends(limit_llm_requests)],
)
async def refresh_summaries(
    principal: CurrentUser, summaries: SummaryDep, settings: SettingsDep
) -> SummaryRefreshResponse:
    """Summarise your busy subjects now rather than at the next scheduled run.

    Your own graph only. At most a handful per request — each is an LLM call.
    """
    if not settings.summaries_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Summaries are turned off.")
    return SummaryRefreshResponse(written=await summaries.refresh(principal.user_id, budget=5))


@router.post(
    "/search", response_model=MemorySearchResponse, dependencies=[Depends(limit_llm_requests)]
)
async def search_memories(
    request: MemorySearchRequest,
    principal: CurrentUser,
    memories: MemoryStoreDep,
    settings: SettingsDep,
) -> MemorySearchResponse:
    """Semantic search over your memories and the shared space."""
    results = await memories.search(
        user_id=visible_owners(principal.user_id, shared=settings.shared_space_enabled),
        query=request.query,
        limit=request.limit,
        statuses=request.statuses,
        categories=request.categories,
    )
    return MemorySearchResponse(
        query=request.query,
        results=[ScoredMemory(memory=m, score=s) for m, s in results],
    )


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    principal: CurrentUser,
    memories: MemoryStoreDep,
    settings: SettingsDep,
    limit: int = Query(200, ge=1, le=1000),
    status_filter: list[MemoryStatus] | None = Query(default=None, alias="status"),
    category: list[MemoryCategory] | None = Query(default=None),
) -> MemoryListResponse:
    """List memories. This is what the graph view will read from."""
    items = await memories.list_all(
        user_id=visible_owners(principal.user_id, shared=settings.shared_space_enabled),
        limit=limit,
        statuses=status_filter,
        categories=category,
    )
    return MemoryListResponse(total=len(items), memories=items)


@router.get("/graph", response_model=GraphResponse)
async def memory_graph(
    principal: CurrentUser,
    memories: MemoryStoreDep,
    settings: SettingsDep,
    limit: int = Query(500, ge=1, le=2000),
    include_archived: bool = Query(False),
    as_of: datetime | None = Query(
        None, description="Show the graph as it stood then: only what was believed."
    ),
) -> GraphResponse:
    """The belief graph, ready for the Three.js view.

    Nodes carry status and confidence (colour and size); edges carry the
    `supersedes` chain that shows how a belief evolved.

    With `as_of`, nodes are the beliefs held at that moment, and one retired
    since then shows as it was then — active. Confidence is today's.
    """
    if as_of is not None and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    statuses = None if include_archived or as_of else [
        MemoryStatus.ACTIVE,
        MemoryStatus.CONTRADICTED,
        MemoryStatus.SUPERSEDED,
    ]
    items = await memories.list_all(
        user_id=visible_owners(principal.user_id, shared=settings.shared_space_enabled),
        limit=limit if not as_of else 100_000,
        statuses=statuses,
    )
    if as_of is not None:
        items = [m for m in items if m.believed_at(as_of)][:limit]
        for m in items:
            if m.status in (MemoryStatus.SUPERSEDED, MemoryStatus.ARCHIVED):
                m.status = MemoryStatus.ACTIVE
    known = {m.id for m in items}

    nodes = [
        GraphNode(
            id=m.id,
            label=m.content,
            category=m.category,
            status=m.status,
            confidence=m.confidence,
            subject=m.subject,
            created_at=m.created_at.isoformat(),
            shared=m.is_shared,
            shared_by_email=m.shared_by_email,
            kind=m.kind,
        )
        for m in items
    ]

    by_id = {m.id: m for m in items}
    edges: list[GraphEdge] = []
    replaced: set[tuple[str, str]] = set()
    for m in items:
        # Read from both ends: a memory shared to the team records its new home
        # only on itself (superseded_by), and that link was never drawn.
        pairs = [(m.id, old_id) for old_id in m.supersedes]
        if m.superseded_by:
            pairs.append((m.superseded_by, m.id))
        for new_id, old_id in pairs:
            if new_id in known and old_id in known and (new_id, old_id) not in replaced:
                replaced.add((new_id, old_id))
                edges.append(GraphEdge(source=new_id, target=old_id, kind="supersedes"))

    seen_conflicts: set[tuple[str, str]] = set()
    for m in items:
        for other_id in m.conflicts_with:
            other = by_id.get(other_id)
            # A dispute is drawn only while both sides are still disputed. An
            # edge left behind on a memory that has since been superseded is
            # history, not an open question — and it hid under the arrow.
            if (
                other is None
                or m.status is not MemoryStatus.CONTRADICTED
                or other.status is not MemoryStatus.CONTRADICTED
            ):
                continue
            key = tuple(sorted((m.id, other_id)))
            if key in seen_conflicts:
                continue  # conflicts are symmetric; draw one edge
            seen_conflicts.add(key)
            edges.append(GraphEdge(source=m.id, target=other_id, kind="conflicts_with"))

    return GraphResponse(nodes=nodes, edges=edges)


@router.post("/{memory_id}/reinforce", response_model=Memory)
async def reinforce_memory(
    memory_id: str, principal: CurrentUser, memories: MemoryStoreDep
) -> Memory:
    """Confirm a memory is still true. Raises confidence and resets its decay clock."""
    return await memories.reinforce(await _owned(memories, memory_id, principal))


@router.post("/{memory_id}/reactivate", response_model=Memory)
async def reactivate_memory(
    memory_id: str, principal: CurrentUser, memories: MemoryStoreDep
) -> Memory:
    """Bring an archived or superseded memory back. Nothing here is a one-way door."""
    memory = await _owned(memories, memory_id, principal)
    if memory.redacted_at:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A forgotten memory cannot return.")
    memory.reactivate()
    memory.reinforce()
    return await memories.save(memory)


@router.post("/{memory_id}/reject", response_model=RejectResponse)
async def reject_memory(
    memory_id: str,
    body: RejectBody,
    principal: CurrentUser,
    memories: MemoryStoreDep,
    corrections: CorrectionDep,
) -> RejectResponse:
    """"This isn't a real fact." Archives the misreading with the reason, restores
    any belief it had retired, and keeps it as an extraction test case."""
    memory = await _owned(memories, memory_id, principal)
    _may_correct(memory, principal)
    try:
        memory, restored = await corrections.reject(
            memory, user_id=principal.user_id, reason=body.reason, note=body.note
        )
    except CorrectionError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc
    return RejectResponse(memory=memory, restored=restored)


@router.post("/{memory_id}/forget", response_model=Memory)
async def forget_memory(
    memory_id: str, principal: CurrentUser, memories: MemoryStoreDep, corrections: CorrectionDep
) -> Memory:
    """Erase what a memory says, everywhere Continuum copied it.

    The record stays — id, dates, edges — so what replaced what still reads, but
    its words, excerpt, subject and vector are gone, as are the copies in
    decision labels and feedback. This cannot be undone.
    """
    memory = await _owned(memories, memory_id, principal)
    _may_correct(memory, principal)
    return await corrections.forget(memory)


@router.get("/{memory_id}", response_model=Memory)
async def get_memory(memory_id: str, principal: CurrentUser, memories: MemoryStoreDep) -> Memory:
    return await _owned(memories, memory_id, principal)


@router.post("/{memory_id}/share", response_model=ShareResponse)
async def share_memory(
    memory_id: str, principal: CurrentUser, memories: MemoryStoreDep, sharing: SharingDep
) -> ShareResponse:
    """Put one of your memories in the shared team space.

    It is resolved against what the team already knows: it may be new, merge with
    an existing shared memory, replace an older one, or raise a shared conflict.
    Your private copy is retired by an edge to the shared one, never deleted.
    """
    memory = await _owned(memories, memory_id, principal)
    if memory.user_id != principal.user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="That memory is already shared.")
    try:
        return await sharing.share(memory, author=principal.user_id, author_email=principal.email)
    except SharingError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
