"""FastAPI dependencies.

Clients are created once at startup and stashed on `app.state`, so every request
reuses the same connection pools.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.config import Settings, get_settings
from continuum.services.chat import ChatService
from continuum.services.decay import DecayService
from continuum.services.extraction import FactExtractor
from continuum.services.ingest import IngestService
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import ResolutionService
from continuum.services.retrieval import RetrievalService


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


def get_qdrant(request: Request) -> QdrantStore:
    return request.app.state.qdrant


def get_memory_store(request: Request) -> MemoryStore:
    return request.app.state.memories


def get_extractor(request: Request) -> FactExtractor:
    return request.app.state.extractor


def get_ingest_service(request: Request) -> IngestService:
    return request.app.state.ingest


def get_resolver(request: Request) -> ResolutionService:
    return request.app.state.resolver


def get_decay_service(request: Request) -> DecayService:
    return request.app.state.decay


def get_retrieval_service(request: Request) -> RetrievalService:
    return request.app.state.retrieval


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat


SettingsDep = Annotated[Settings, Depends(get_settings)]
LLMDep = Annotated[LLMClient, Depends(get_llm)]
QdrantDep = Annotated[QdrantStore, Depends(get_qdrant)]
MemoryStoreDep = Annotated[MemoryStore, Depends(get_memory_store)]
ExtractorDep = Annotated[FactExtractor, Depends(get_extractor)]
IngestDep = Annotated[IngestService, Depends(get_ingest_service)]
ResolverDep = Annotated[ResolutionService, Depends(get_resolver)]
DecayDep = Annotated[DecayService, Depends(get_decay_service)]
RetrievalDep = Annotated[RetrievalService, Depends(get_retrieval_service)]
ChatDep = Annotated[ChatService, Depends(get_chat_service)]
