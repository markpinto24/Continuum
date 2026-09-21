"""Application configuration.

Everything is env-driven so the same image runs locally (Ollama + Qdrant in Docker)
and in production (vLLM / hosted OpenAI-compatible endpoint + managed Qdrant).
"""

from functools import lru_cache

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
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

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
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_api_key: str = "ollama"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    # --- Memory policy -----------------------------------------------------
    # Initial confidence assigned to a freshly extracted memory.
    default_confidence: float = 0.70
    # Cosine similarity above which two memories are treated as the same fact
    # (Phase 1: skip as duplicate. Phase 2: hand to the resolution layer).
    duplicate_similarity_threshold: float = 0.94
    # Similarity band that signals "related, possibly contradictory" — the
    # candidate set the Phase 2 resolver will reason over.
    conflict_similarity_threshold: float = 0.78
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

    @property
    def is_local(self) -> bool:
        return self.environment.lower() in {"local", "dev", "development"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
