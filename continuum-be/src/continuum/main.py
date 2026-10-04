"""Continuum API entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from continuum.api.router import api_router
from continuum.clients.authdb import AuthDB
from continuum.clients.labels import LabelStore
from continuum.clients.learning import LearningStore
from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.config import get_settings
from continuum.core.logger import configure_logging, get_logger
from continuum.core.middleware import RequestContextMiddleware
from continuum.db.engine import make_engine, migrate
from continuum.services.auth import AuthService
from continuum.services.chat import ChatService
from continuum.services.corrections import CorrectionService
from continuum.services.decay import DecayService
from continuum.services.extraction import FactExtractor
from continuum.services.feedback import Calibration, FeedbackService
from continuum.services.ingest import IngestService
from continuum.services.limits import SlidingWindow
from continuum.services.memory_store import MemoryStore
from continuum.services.reindex import EmbeddingMigration
from continuum.services.resolution import ResolutionService
from continuum.services.retrieval import RetrievalService
from continuum.services.sharing import SharingService
from continuum.services.speech import SpeechService
from continuum.services.summaries import SummaryService
from continuum.services.voice import SynthesisService

log = get_logger("continuum.app")


def _build_scheduler(decay: DecayService, summaries: SummaryService) -> AsyncIOScheduler:
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

    async def run_summaries() -> None:
        try:
            log.info("summary.scheduled_run", written=await summaries.refresh_all())
        except Exception:
            log.exception("summary.scheduled_run_failed")

    if settings.summaries_enabled:
        scheduler.add_job(
            run_summaries,
            trigger=IntervalTrigger(hours=settings.summary_interval_hours),
            id="summaries",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()

    # Accounts first: a missing or unmigratable database should stop the API
    # before it re-embeds anything or accepts a single request. The schema is
    # upgraded here, so there is no separate migrate step to run by hand.
    engine = make_engine(settings)
    await migrate(engine)
    authdb = AuthDB(engine)
    auth = AuthService(authdb, settings)
    await auth.bootstrap_from_settings()

    # What people have taught it: decisions, rules, the judge's calibration.
    calibration = Calibration()
    learning = LearningStore(engine)
    feedback = FeedbackService(
        LabelStore(engine), settings, learning=learning, calibration=calibration
    )
    await feedback.refresh_calibration()

    llm = LLMClient(settings)
    qdrant = QdrantStore(settings)
    await qdrant.ensure_collection()
    # Re-embed from the previously active collection if the embedding model
    # changed. Runs before the API reports healthy, so compose's depends_on
    # holds traffic until the memories are all in the new vector space.
    migration = await EmbeddingMigration(qdrant, llm).run()

    memories = MemoryStore(qdrant, llm, settings)
    extractor = FactExtractor(llm)
    resolver = ResolutionService(memories, llm, settings, calibrate=calibration.map)
    decay = DecayService(memories, settings)
    summaries = SummaryService(memories, llm, settings)
    retrieval = RetrievalService(memories, settings)
    ingest = IngestService(
        extractor, memories, resolver, llm, settings, rules=feedback.compatible_subjects
    )

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
    app.state.authdb = authdb
    app.state.auth = auth
    app.state.llm_limiter = SlidingWindow(settings.llm_requests_per_minute, 60)
    app.state.feedback = feedback
    app.state.learning = learning
    app.state.summaries = summaries
    app.state.corrections = CorrectionService(
        memories, labels=feedback.store, learning=learning
    )
    app.state.sharing = SharingService(ingest, memories, settings)

    speech = SpeechService(settings)
    app.state.speech = speech
    voice = SynthesisService(settings)
    app.state.voice = voice
    app.state.tts_limiter = SlidingWindow(settings.tts_requests_per_minute, 60)
    # Background, not awaited: a first-run model download must not hold up the
    # API's healthcheck. Dictation before it finishes just waits for the load.
    # Pass the methods, not coroutines: a coroutine created for a preload that
    # is switched off would never be awaited.
    preloads = [
        asyncio.create_task(load())
        for load, wanted in (
            (speech.preload, settings.speech_enabled and settings.speech_preload),
            (voice.preload, settings.tts_enabled and settings.tts_preload),
        )
        if wanted
    ]

    scheduler: AsyncIOScheduler | None = None
    if settings.decay_enabled:
        scheduler = _build_scheduler(decay, summaries)
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
        for task in preloads:
            if not task.done():
                task.cancel()
        if scheduler:
            scheduler.shutdown(wait=False)
        await llm.aclose()
        await qdrant.aclose()
        await engine.dispose()
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
        version="0.4.0",
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
