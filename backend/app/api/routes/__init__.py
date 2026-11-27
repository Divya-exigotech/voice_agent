from fastapi import APIRouter

from app.api.routes import (
    auth,
    conversation,
    customers,
    monitoring,
    queue,
    sessions,
    voice,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(customers.router)
api_router.include_router(queue.router)
api_router.include_router(sessions.router)
api_router.include_router(conversation.router)
api_router.include_router(voice.router)
api_router.include_router(monitoring.router)
