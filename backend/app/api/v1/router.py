from fastapi import APIRouter

from app.api.v1.endpoints import customers, health, voice

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(customers.router, tags=["customers"])
api_router.include_router(voice.router, tags=["voice"])
