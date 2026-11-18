from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "AI Voice Calling Platform"
    ENVIRONMENT: str = "local"
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    DATABASE_URL: str = "postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/voice_agent"

    # Single JSON source for the demo customer/invoice queue (synthetic data only).
    CUSTOMERS_FILE: Path = BACKEND_DIR / "data" / "customers.json"


settings = Settings()
