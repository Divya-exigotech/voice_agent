from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.services.conversation import ConversationService
from app.services.followup import FollowUpScheduler
from app.services.monitoring import MonitoringStore
from app.services.session_manager import SessionManager


async def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager


async def get_conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


async def get_followup_scheduler(request: Request) -> FollowUpScheduler:
    return request.app.state.followup_scheduler


async def get_monitoring_store(request: Request) -> MonitoringStore:
    store = getattr(request.app.state, "monitoring", None)
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Live monitoring needs the PostgreSQL database (DATABASE_URL is not set)",
        )
    return store


FollowUpSchedulerDep = Annotated[FollowUpScheduler, Depends(get_followup_scheduler)]
SessionManagerDep = Annotated[SessionManager, Depends(get_session_manager)]
ConversationServiceDep = Annotated[
    ConversationService, Depends(get_conversation_service)
]
MonitoringStoreDep = Annotated[MonitoringStore, Depends(get_monitoring_store)]
