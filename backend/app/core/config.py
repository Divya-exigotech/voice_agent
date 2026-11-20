from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Azure's DefaultAzureCredential reads AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET
# from the process environment, which pydantic-settings does not populate. Real environment
# variables still win (override=False).
load_dotenv(BACKEND_DIR / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "AI Voice Calling Platform"
    ENVIRONMENT: str = "local"
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    DATABASE_URL: str = "postgresql://user:password@host:5432/calling_agnet"

    # Azure AI Foundry voice agent. Values come from backend/.env only; never hardcode them.
    AZURE_AI_PROJECT_ENDPOINT: str = ""
    AZURE_AI_AGENT_NAME: str = ""
    # Pass customer facts as structured inputs (needs matching placeholders in the agent).
    AZURE_AI_USE_STRUCTURED_INPUTS: bool = False
    # Ask the agent to speak first when a voice session opens.
    AZURE_AI_REQUEST_GREETING: bool = True
    VOICE_SESSION_MAX_SECONDS: int = 600

    # Single JSON source for the demo customer/invoice queue (synthetic data only).
    CUSTOMERS_FILE: Path = BACKEND_DIR / "data" / "customers.json"


settings = Settings()
