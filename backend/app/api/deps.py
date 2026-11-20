from typing import Annotated

from fastapi import Depends, Request

from app.services.conversation import ConversationService
from app.services.followup import FollowUpScheduler
from app.services.session_manager import SessionManager


def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager


def get_conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


def get_followup_scheduler(request: Request) -> FollowUpScheduler:
    return request.app.state.followup_scheduler


FollowUpSchedulerDep = Annotated[FollowUpScheduler, Depends(get_followup_scheduler)]
SessionManagerDep = Annotated[SessionManager, Depends(get_session_manager)]
ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
