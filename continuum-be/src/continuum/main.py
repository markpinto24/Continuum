"""Continuum API entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from continuum.api.router import api_router
from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.config import get_settings
from continuum.core.logging import configure_logging
from continuum.core.middleware import RequestContextMiddleware
from continuum.services.chat import ChatService
from continuum.services.decay import DecayService
from continuum.services.extraction import FactExtractor
from continuum.services.ingest import IngestService
from continuum.services.memory_store import MemoryStore
from continuum.services.reindex import EmbeddingMigration
from continuum.services.resolution import ResolutionService
from continuum.services.retrieval import RetrievalService

log = structlog.get_logger("continuum.app")


def _build_scheduler(decay: DecayService) -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def run_decay() -> None:
        try:
            report = await decay.sweep_all()
            log.info("decay.scheduled_sweep", **report.model_dump())
        except Exception:
            # A failed sweep must never take the API down with it.
            log.exception("decay.scheduled_sweep_failed")

    scheduler.add_job(
        run_decay,
        trigger=IntervalTrigger(minutes=settings.decay_interval_minutes),
        id="decay_sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,  # a missed window runs once, not N times
    )
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()

    llm = LLMClient(settings)
    qdrant = QdrantStore(settings)
    await qdrant.ensure_collection()
    # Re-embed from the previously active collection if the embedding model
    # changed. Runs before the API reports healthy, so compose's depends_on
    # holds traffic until the memories are all in the new vector space.
    migration = await EmbeddingMigration(qdrant, llm).run()

    memories = MemoryStore(qdrant, llm, settings)
    extractor = FactExtractor(llm)
    resolver = ResolutionService(memories, llm, settings)
    decay = DecayService(memories, settings)
    retrieval = RetrievalService(memories, settings)
    ingest = IngestService(extractor, memories, resolver, llm, settings)

    app.state.settings = settings
    app.state.llm = llm
    app.state.qdrant = qdrant
    app.state.memories = memories
    app.state.extractor = extractor
    app.state.resolver = resolver
    app.state.decay = decay
    app.state.ingest = ingest
    app.state.retrieval = retrieval
    app.state.chat = ChatService(retrieval, ingest, llm, settings)

    scheduler: AsyncIOScheduler | None = None
    if settings.decay_enabled:
        scheduler = _build_scheduler(decay)
        scheduler.start()
    app.state.scheduler = scheduler

    log.info(
        "continuum.started",
        environment=settings.environment,
        llm_model=settings.llm_model,
        embedding_model=settings.embedding_model,
        collection=qdrant.collection,
        reindexed=migration.memories if migration.migrated else 0,
        decay_enabled=settings.decay_enabled,
        auto_supersede_gate=settings.auto_supersede_confidence,
    )

    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        await llm.aclose()
        await qdrant.aclose()
        log.info("continuum.stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.app_name} API",
        description=(
            "Continuum — long-term work memory for AI agents. Remembers decisions, "
            "preferences and constraints, tracks why they changed, and escalates "
            "genuine contradictions instead of guessing."
        ),
        version="0.3.0",
        lifespan=lifespan,
    )

    # Order matters: request context is bound outermost so every downstream log
    # line — including CORS rejections — carries the request id.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"app": settings.app_name, "docs": "/docs", "api": settings.api_prefix}

    return app


app = create_app()
