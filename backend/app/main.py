from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.services.foundry_session import foundry_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the Foundry project client once and close it on shutdown."""
    app.state.foundry_client = None
    async with AsyncExitStack() as stack:
        if settings.AZURE_AI_PROJECT_ENDPOINT:
            app.state.foundry_client = await stack.enter_async_context(
                foundry_client(settings.AZURE_AI_PROJECT_ENDPOINT)
            )
        yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)
