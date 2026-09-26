from fastapi import APIRouter

from continuum.api.routes import (
    admin,
    auth,
    chat,
    conflicts,
    decay,
    health,
    ingest,
    memories,
    speech,
)

api_router = APIRouter()
# Public: health (for the Docker healthcheck) and the sign-in endpoints inside
# `auth`. Every other route takes a `CurrentUser` and refuses a request without
# a valid session or API key.
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(ingest.router)
api_router.include_router(memories.router)
api_router.include_router(conflicts.router)
api_router.include_router(decay.router)
api_router.include_router(chat.router)
api_router.include_router(speech.router)
