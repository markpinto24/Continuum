from fastapi import APIRouter

from continuum.api.routes import chat, conflicts, decay, health, ingest, memories

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(ingest.router)
api_router.include_router(memories.router)
api_router.include_router(conflicts.router)
api_router.include_router(decay.router)
api_router.include_router(chat.router)
