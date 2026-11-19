# AI Voice Calling Platform

Multi-organization SaaS for configurable AI-powered outbound customer calls.

## Stack
- **Frontend:** React + Vite + TypeScript, Tailwind CSS, React Router, TanStack Query (`frontend/`)
- **Backend:** Python FastAPI, SQLAlchemy 2, Alembic (`backend/`)
- **Database:** PostgreSQL (local install)

## Structure
```
backend/
  app/
    api/v1/endpoints/   # REST routes
    core/               # settings, security
    db/                 # engine, session, Base
    models/             # SQLAlchemy models
    schemas/            # Pydantic schemas
    services/           # business logic
    integrations/       # org API data ingestion/sync
    agents/tools/       # AI agent tool functions
    providers/          # telephony + voice provider abstractions
    workers/            # scheduler / call / post-call workers
  alembic/              # migrations
  tests/
frontend/
  src/ api, components, pages, hooks, types, lib
```

## Run locally
```bash
# 1. Database
# Create database `voice_agent` in your local PostgreSQL (e.g. via DBeaver)
# and set DATABASE_URL in backend/.env

# 2. Backend  (http://localhost:8000/docs)
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload

# 3. Frontend  (http://localhost:5173)
cd frontend
npm install
npm run dev
```
