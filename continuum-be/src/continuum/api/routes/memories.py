from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from continuum.api.deps import MemoryStoreDep
from continuum.models.memory import Memory, MemoryCategory, MemoryStatus
from continuum.models.schemas import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    MemoryListResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    ScoredMemory,
)

router = APIRouter(prefix="/memories", tags=["memories"])


@router.post("/search", response_model=MemorySearchResponse)
async def search_memories(
    request: MemorySearchRequest, memories: MemoryStoreDep
) -> MemorySearchResponse:
    """Semantic search over a user's memories."""
    results = await memories.search(
        user_id=request.user_id,
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
    memories: MemoryStoreDep,
    user_id: str = Query(..., min_length=1),
    limit: int = Query(200, ge=1, le=1000),
    status_filter: list[MemoryStatus] | None = Query(default=None, alias="status"),
    category: list[MemoryCategory] | None = Query(default=None),
) -> MemoryListResponse:
    """List memories. This is what the graph view will read from."""
    items = await memories.list_all(
        user_id=user_id, limit=limit, statuses=status_filter, categories=category
    )
    return MemoryListResponse(total=len(items), memories=items)


@router.get("/graph", response_model=GraphResponse)
async def memory_graph(
    memories: MemoryStoreDep,
    user_id: str = Query(..., min_length=1),
    limit: int = Query(500, ge=1, le=2000),
    include_archived: bool = Query(False),
) -> GraphResponse:
    """The belief graph, ready for the Three.js view.

    Nodes carry status and confidence (colour and size); edges carry the
    `supersedes` chain that shows how a belief evolved.
    """
    statuses = None if include_archived else [
        MemoryStatus.ACTIVE,
        MemoryStatus.CONTRADICTED,
        MemoryStatus.SUPERSEDED,
    ]
    items = await memories.list_all(user_id=user_id, limit=limit, statuses=statuses)
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
        )
        for m in items
    ]

    edges: list[GraphEdge] = []
    seen_conflicts: set[tuple[str, str]] = set()
    for m in items:
        for old_id in m.supersedes:
            if old_id in known:
                edges.append(GraphEdge(source=m.id, target=old_id, kind="supersedes"))
        for other_id in m.conflicts_with:
            if other_id not in known:
                continue
            key = tuple(sorted((m.id, other_id)))
            if key in seen_conflicts:
                continue  # conflicts are symmetric; draw one edge
            seen_conflicts.add(key)
            edges.append(GraphEdge(source=m.id, target=other_id, kind="conflicts_with"))

    return GraphResponse(nodes=nodes, edges=edges)


@router.post("/{memory_id}/reinforce", response_model=Memory)
async def reinforce_memory(memory_id: str, memories: MemoryStoreDep) -> Memory:
    """Confirm a memory is still true. Raises confidence and resets its decay clock."""
    memory = await memories.get(memory_id)
    if not memory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return await memories.reinforce(memory)


@router.post("/{memory_id}/reactivate", response_model=Memory)
async def reactivate_memory(memory_id: str, memories: MemoryStoreDep) -> Memory:
    """Bring an archived or superseded memory back. Nothing here is a one-way door."""
    memory = await memories.get(memory_id)
    if not memory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    memory.reactivate()
    memory.reinforce()
    return await memories.save(memory)


@router.get("/{memory_id}", response_model=Memory)
async def get_memory(memory_id: str, memories: MemoryStoreDep) -> Memory:
    memory = await memories.get(memory_id)
    if not memory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return memory
