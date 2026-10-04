from __future__ import annotations

from fastapi import APIRouter

from continuum.api.deps import AuthDBDep, LLMDep, QdrantDep, SettingsDep
from continuum.models.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(
    settings: SettingsDep, qdrant: QdrantDep, llm: LLMDep, authdb: AuthDBDep
) -> HealthResponse:
    qdrant_ok = await qdrant.ping()
    llm_ok = await llm.ping()
    # Without the database nobody can sign in, so it counts toward "ok".
    database_ok = await authdb.ping()
    return HealthResponse(
        status="ok" if (qdrant_ok and llm_ok and database_ok) else "degraded",
        app=settings.app_name,
        environment=settings.environment,
        qdrant="up" if qdrant_ok else "down",
        llm="up" if llm_ok else "down",
        database="up" if database_ok else "down",
    )
