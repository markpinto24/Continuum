"""Application configuration.

Everything is env-driven so the same image runs locally (Ollama + Qdrant in Docker)
and in production (vLLM / hosted OpenAI-compatible endpoint + managed Qdrant).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---------------------------------------------------------------
    app_name: str = "Continuum"
    environment: str = "local"
    debug: bool = True
    log_level: str = "INFO"
    # "line": one coloured, human-readable line per event (Level, Timestamp,
    # Module, Function, Message + fields). "json": one object per line, for a log
    # aggregator. Both go through the same redaction; see core/logger.py.
    log_format: Literal["line", "json"] = "line"
    # ANSI colour by level in the line format. Turn off when logs go to a file
    # or a system that shows escape codes literally.
    log_colors: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # --- Relational database -----------------------------------------------
    # Users, sessions and API keys live in Postgres, not Qdrant: they need unique
    # constraints and transactions, and a lost vector index must never be able to
    # take the credentials with it. Postgres rather than a local file so several
    # API processes can share one account store. Any SQLAlchemy async URL works;
    # the tests use sqlite+aiosqlite so they need no running service.
    # The schema is owned by Alembic and upgraded on startup (db/migrate.py).
    database_url: str = "postgresql+asyncpg://continuum:continuum@localhost:5432/continuum"
    # Connections held open per API process. Sign-in, session and key checks are
    # single indexed queries, so a small pool goes a long way; raise it with the
    # worker count, keeping the total under Postgres's max_connections.
    database_pool_size: int = 5
    database_max_overflow: int = 5

    # --- Authentication ----------------------------------------------------
    # How long a web sign-in lasts. Absolute, not sliding: a stolen cookie stops
    # working on a fixed date however actively it is used.
    session_ttl_hours: int = 24 * 14
    session_cookie_name: str = "continuum_session"
    # None = decide per request (Secure when the request arrived over HTTPS,
    # directly or via X-Forwarded-Proto). Force True behind a TLS proxy that
    # does not forward the scheme; never force False in production.
    session_cookie_secure: bool | None = None
    # A browser request authenticated by cookie must carry this header on every
    # unsafe method. A custom header cannot be set cross-site without a CORS
    # preflight, which the CORS policy refuses — so a forged form post fails.
    csrf_header: str = "x-continuum-client"
    # Behind a reverse proxy every request arrives from the proxy's address, so
    # the sign-in lockout would count all users as one client. When true, the
    # X-Real-IP header identifies the client instead. Only enable behind a proxy
    # that SETS that header (overwriting any a client sent) and when the API is
    # reachable solely through it — otherwise a caller can forge its address.
    # The shipped setup has no such proxy (the UI runs on the Vite dev server),
    # so it stays off; the per-email lockout applies either way.
    trust_proxy_headers: bool = False
    password_min_length: int = 10
    # Failed sign-ins allowed per email and per client address before a lockout.
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    # While no account exists, the web UI offers "create the first admin". Turn
    # off on a deployment reachable by strangers and bootstrap from the env pair
    # below instead — otherwise whoever loads the page first owns the instance.
    auth_allow_web_setup: bool = True
    # Creates the first admin at startup when no account exists yet.
    admin_email: str | None = None
    admin_password: str | None = None
    # Memory owner id for that admin. Set it to adopt memories stored before
    # authentication existed (they are keyed by whatever user_id was sent then).
    admin_user_id: str | None = None

    # --- Abuse limits ------------------------------------------------------
    # Requests per user per minute to endpoints that spend an LLM or embedding
    # call (ingest, chat, retrieval preview, search). Local models are slow and
    # hosted ones bill per token; one runaway agent should not starve the rest.
    llm_requests_per_minute: int = 30
    # Largest note or transcript accepted by ingest and chat, in characters.
    max_input_chars: int = 50_000

    # --- Speech to text ----------------------------------------------------
    # Dictation in the chat box. Transcribed HERE, by a local Whisper model —
    # never by the browser, whose built-in recognition streams audio to Google
    # (and in Brave is switched off entirely). Audio is transcribed in memory
    # and discarded; only the text the user then sends is kept.
    speech_enabled: bool = True
    # tiny ~75 MB / base ~145 MB / small ~480 MB / medium ~1.5 GB. Bigger is more
    # accurate and slower. base is quick on a CPU and good on clear speech.
    # Downloaded once to the Hugging Face cache (a volume in Docker).
    speech_model: str = "base"
    speech_device: str = "cpu"  # "cuda" with an NVIDIA GPU and a CUDA build of ctranslate2
    # int8 on CPU: ~4x smaller and faster than float32, accuracy all but equal.
    speech_compute_type: str = "int8"
    # Fixed language skips detection, which is slow and unreliable on a short
    # clip. None = detect per recording (for multilingual users).
    speech_language: str | None = "en"
    speech_beam_size: int = 5
    # Longest recording accepted. Dictation is a message, not a meeting: a cap
    # keeps one upload from holding the CPU for minutes.
    speech_max_seconds: int = 120
    speech_max_bytes: int = 10 * 1024 * 1024
    # Transcriptions run one at a time: each already uses every core, and two
    # together are slower than two in turn.
    speech_concurrency: int = 1
    # Load the model in the background at startup, so the first dictation does
    # not wait for a download. Startup itself does not wait for it.
    speech_preload: bool = True

    # --- Qdrant ------------------------------------------------------------
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "continuum_memories"

    # --- LLM (any OpenAI-compatible endpoint) ------------------------------
    # Ollama:  http://localhost:11434/v1        key: "ollama"
    # vLLM:    http://localhost:8000/v1         key: "EMPTY"
    # Groq:    https://api.groq.com/openai/v1   key: <groq key>
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1536
    llm_timeout_seconds: float = 120.0

    # --- Embeddings --------------------------------------------------------
    # bge-m3 since Phase 5: nomic-embed-text returned byte-identical vectors for
    # "Postgres" vs "MongoDB" (FINDINGS §1). bge-m3 passes the preflight and
    # scored best of four candidates on the resolution corpus.
    #
    # Changing the model is safe: each model gets its own collection, and on
    # startup every memory is re-embedded from the previously active one
    # (services/reindex.py). Nothing is deleted, and switching back works.
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_api_key: str = "ollama"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024

    # --- Memory policy -----------------------------------------------------
    # Initial confidence assigned to a freshly extracted memory.
    default_confidence: float = 0.70
    # The two cosine bands. These are properties of the EMBEDDING MODEL, not of
    # the product, so by default they come from CALIBRATED_THRESHOLDS below for
    # whichever model is configured. Set either explicitly to override.
    #
    # duplicate: above it, a pair skips the judge and the incoming fact is
    #   discarded — unless the duplicate guard sees a changed number, date,
    #   name or negation. Too low and distinct facts are merged away.
    # conflict: the lowest score at which a candidate is judged at all. Too
    #   high and contradictions are never examined (band misses).
    duplicate_similarity_threshold: float | None = None
    conflict_similarity_threshold: float | None = None
    retrieval_top_k: int = 8
    # How many existing memories the resolver will reason over per new fact.
    resolution_candidate_limit: int = 5

    # --- Resolution policy (Phase 2) ---------------------------------------
    # The judge must be at least this sure before we retire a memory without
    # asking. Below it, a "supersedes" verdict is downgraded to a human
    # escalation. This is the single most important knob in the system: raise it
    # and you escalate more, lower it and you silently lose beliefs.
    auto_supersede_confidence: float = 0.80
    # Confidence granted to a memory that just won a supersede.
    superseded_winner_confidence: float = 0.85

    # Retire a memory about a ROLE (owns, maintains, reviews, leads...) only when
    # the NEW statement says the earlier holder stopped ("now", "no longer",
    # "has taken over", "moved off"). Keyed on words, not category — the
    # extractor files ownership as `fact`.
    # A bare second name — "Raj owns billing" after "Sara owns billing" — is
    # escalated instead: owners, maintainers and reviewers are usually shared,
    # and a 7B judge reads every one as a handover at probability 1.0, where no
    # gate can stop it. Belief loss 8.7% -> 0% on 46 cases, including a held-out
    # set written to test this rule before it ran (FINDINGS §10). Costs a
    # handover stated without change words ("Pat is the QA contact"), which is
    # escalated instead of applied.
    person_supersede_requires_change_language: bool = True
    # Confidence bump applied when an existing memory is re-observed.
    reinforcement_step: float = 0.05

    # --- Decay policy (Phase 2) --------------------------------------------
    decay_enabled: bool = True
    decay_interval_minutes: int = 360        # how often the sweep runs
    decay_archive_threshold: float = 0.25    # below this, a memory is archived
    decay_floor: float = 0.05                # never decay below this
    decay_batch_limit: int = 2000

    # --- Chat & retrieval (Phase 3) ----------------------------------------
    # Memories injected into a chat turn. Small on purpose: a wall of context
    # makes a small model paraphrase the memory back instead of answering.
    chat_memory_limit: int = 6
    # We over-fetch by similarity and then re-rank, because the rank order after
    # applying confidence and recency is not the order Qdrant returned.
    chat_candidate_multiplier: int = 4
    # Below this cosine score a memory is noise, not context.
    chat_min_similarity: float = 0.35
    # Turns kept from the client's history. Older turns are dropped, not
    # summarised — the durable content of an old turn belongs in memory, which is
    # the whole point of the system.
    chat_max_history: int = 12
    chat_temperature: float = 0.3
    # Run each chat turn back through ingest, so a conversation both confirms
    # existing memories and records new ones. See ChatService._remember.
    chat_remember_turns: bool = True

    # Retrieval rank = similarity x confidence^w_conf x recency^w_rec.
    # Exponents rather than a weighted sum: each factor keeps its own shape, and
    # setting a weight to 0 cleanly disables that factor for A/B evaluation.
    retrieval_confidence_weight: float = 1.0
    retrieval_recency_weight: float = 1.0
    # Deliberately NOT the per-category decay half-life. Decay answers "do we
    # still believe this"; recency answers "is this topical right now". A
    # two-year-old event is still true and still less relevant.
    retrieval_recency_half_life_days: float = 45.0
    # Floor so an old memory is down-weighted, never erased, by age alone.
    retrieval_recency_floor: float = 0.30

    @model_validator(mode="after")
    def _thresholds_for_model(self) -> Settings:
        """Fill unset thresholds from the calibration table for this model.

        An uncalibrated model falls back to the historical 0.94 / 0.78, which
        were never calibrated against anything — run
        `scripts/run_eval.py --calibrate` and add the model to the table.
        """
        duplicate, conflict = CALIBRATED_THRESHOLDS.get(
            self.embedding_model.split(":")[0], UNCALIBRATED_THRESHOLDS
        )
        if self.duplicate_similarity_threshold is None:
            self.duplicate_similarity_threshold = duplicate
        if self.conflict_similarity_threshold is None:
            self.conflict_similarity_threshold = conflict
        return self

    @property
    def is_local(self) -> bool:
        return self.environment.lower() in {"local", "dev", "development"}


#: (duplicate, conflict) per embedding model, from `run_eval.py --calibrate` on
#: the Phase 5 corpus with the duplicate guard in place (FINDINGS §8). The rule:
#: the conflict band sits just below the lowest pair that must be judged; the
#: duplicate band sits just above every non-duplicate the guard cannot see.
CALIBRATED_THRESHOLDS: dict[str, tuple[float, float]] = {
    "bge-m3": (0.86, 0.45),
    "mxbai-embed-large": (0.86, 0.52),
    "all-minilm": (0.91, 0.19),
    "nomic-embed-text": (0.90, 0.66),
}
UNCALIBRATED_THRESHOLDS: tuple[float, float] = (0.94, 0.78)


@lru_cache
def get_settings() -> Settings:
    return Settings()
