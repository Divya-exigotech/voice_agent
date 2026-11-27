# Payment Follow-up Agent PoC

Browser-based voice agent PoC for payment follow-ups, using a model deployed in Azure AI Foundry.
See [plan.md](plan.md) for scope and milestones. Telephony is out of scope until M6.

**Status: M5 implemented. M2–M5 need a live run against your Azure resources** (see [docs/DEMO.md](docs/DEMO.md)).
- **M1:** JSON customer queue, dashboard, and sequential progression.
- **M2:** typed conversation with your **existing Microsoft Foundry voice agent**, connected by name with Microsoft Entra ID. The backend adds the current customer's facts to each session, and outcomes recorded by the agent's `record_outcome` tool are validated by the backend.
- **M3:** browser voice. The operator and the agent speak and hear each other through the browser microphone and speakers. The backend relays the audio over a WebSocket and captures the transcript for both speakers.
- **M4:** every finalized session's transcript JSON is saved to a **private Azure Blob container**, signed in with Entra ID. The queue advances only after the save succeeds, and the dashboard can read the saved transcript back.
- **M5:** validation and demo polish:
  - review flags on every agent turn (credential requests, unverified facts, pressure);
  - redaction of codes and card numbers customers share;
  - a scenario evaluation harness that runs against the real agent;
  - readiness chips, a session timer and an in-app demo guide;
  - a [demo runbook](docs/DEMO.md) and a [privacy, consent and retention review](docs/PRIVACY_REVIEW.md).

Telephony (M6) is deferred until the browser PoC is approved.

## Layout

```text
data/customers.json        Single source of synthetic customer/invoice records (processed in file order)
backend/                   FastAPI API
  app/main.py              App factory (loads + validates customers.json, builds the model client)
  app/api/routes/          /api/auth, /api/customers, /api/queue, /api/sessions, /messages, /voice
  app/schemas/             Pydantic models (customer, session, transcript, chat)
  app/services/
    customer_repository.py JSON loading/validation
    customer_queue.py      Sequential queue: one session at a time, advance exactly once
    session_manager.py     Session metadata, server-side turns, outcome proposals, finalization
    agent_prompt.py        Per-session customer context (from the trusted JSON record) + record_outcome tool schema
    outcome_validation.py  Backend validation of agent-proposed outcomes/dates
    response_checks.py     Screening of agent turns (review flags) and redaction of customer secrets
    voice_agent.py         Live connection to the Foundry voice agent: event pump, transcripts, tool calls
    conversation.py        One live agent session per app session (open, reuse, reconnect, close)
    transcript_storage.py  Transcript storage: Azure Blob (private container) or in-memory for development
    auth_store.py          Persistent local user accounts and JWT signing key
  scripts/verify_agent.py  Live check: connect to the agent, show its tools, get the opening line and a reply
  scripts/setup_blob.py    Create/verify the private transcript container (sign-in, privacy, write/read/delete probe)
  app/evaluation/          Customer-reply scenarios and the runner used by scripts/evaluate_agent.py
  scripts/evaluate_agent.py  Run the scenarios against the real agent; JSON report in backend/reports/
docs/DEMO.md               Demo runbook: pre-flight checks, script, lines to say, troubleshooting
docs/PRIVACY_REVIEW.md     Data inventory, controls, gaps and sign-off checklist before real data
infra/blob-lifecycle-policy.json  Example retention policy (cool after 30 days, delete after 90)
  tests/                   pytest (voice agent is faked; no network needed)
frontend/                  Next.js 16 (App Router, TypeScript)
  app/page.tsx              Login and registration page (`/`)
  app/dashboard/           Protected dashboard page
  proxy.ts                  Redirects unauthenticated visitors to sign in
  components/              Dashboard, CurrentCustomerCard, SequenceProgress, VoiceSessionPanel,
                           TranscriptPanel, ChatComposer, LastSessionCard
  lib/api.ts               Calls same-origin /api/*, which Next.js proxies to FastAPI
  lib/voice/               voiceSession.ts (mic + WebSocket), player.ts (agent audio), pcm.ts (resample/PCM16)
  public/worklets/         pcm-capture.js AudioWorklet (microphone frames)
  __tests__/               Vitest + Testing Library
```

The customer file lives at the repo root, not in `frontend/public/`. The backend validates it and serves it via the API, so
customer data is never published as a static asset. Override the path with `CUSTOMERS_JSON_PATH`.

## Prerequisites

- Python 3.11+
- Node.js 20.9+ (developed on Node 24)
- An existing **voice agent** in a Microsoft Foundry project
- A way for `DefaultAzureCredential` to sign you in locally, such as the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (`az login`)
- Access to the Foundry project for your account

## User registration and login

Users can register with their name, email and a password of at least 12 characters, then sign in with that
email/password. The backend exposes `POST /api/auth/register` and `POST /api/auth/login`; both return a one-hour
HS256 JWT (`access_token`) and set the same token in an HttpOnly cookie for the browser. The browser uses that
cookie for dashboard API calls; API clients can instead send `Authorization: Bearer <access_token>`.
`GET /api/auth/session` validates the current browser session and `POST /api/auth/logout` clears its cookie.

User records and the persistent JWT signing key are stored in
`~/.voice-agent/users.sqlite3` (on Windows, under the current user's home directory). This does not require
editing `.env`. Back up this file and use persistent storage for it when deploying; losing the signing key
invalidates existing tokens. Self-registration is open, and registered accounts access the same shared dashboard;
per-user data separation and role-based authorization are not implemented.

## Configure the Foundry voice agent (Entra ID, no keys)

1. Set these in `.env` at the repo root:
   - `AZURE_AI_PROJECT_ENDPOINT`: the project endpoint, such as `https://<resource>.services.ai.azure.com/api/projects/<project>`.
   - `AZURE_AI_AGENT_NAME`: the agent's name.
2. Add the **`record_outcome` function tool** to the agent in Foundry so it can report outcomes.
   - Print the tool JSON with `python -m scripts.verify_agent --print-tool`.
   - Add it to the agent's tools with tool choice `auto`.
   - The tool is executed by this backend, which validates the arguments; Foundry doesn't run it.
   - Without the tool, the conversation still works, but every session is saved as `completed_no_commitment`.
3. Sign in. Choose one with `AZURE_AUTH_METHOD` in `.env`:
   - `default`: run `az login` (or `az login --tenant <tenant-id>` if you have several tenants).
   - `browser`: no Azure CLI needed. The first call opens a browser window to sign in. The sign-in is cached, so restarts don't ask again. Set `AZURE_TENANT_ID` if your account belongs to several tenants.
   - `client_secret`: a service principal. Set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET`.

   Whichever identity you use needs the **Azure AI User** role on the Foundry project. Restart the backend after changing `.env`, because `--reload` doesn't watch it.
4. Verify:

   ```powershell
   cd backend
   python -m scripts.verify_agent --say "I will pay on Friday"
   ```

   The script:
   - connects to the agent by name;
   - prints the model, output modalities and tools the session reports, and warns if `record_outcome` is missing;
   - adds the first customer's context;
   - prints the agent's opening line, its reply, and any tool call.

**How the backend uses the agent:**
- It connects with `client.voice_agents.realtime.connect(agent_name=...)` from `azure-ai-projects[voice]`, the same as Microsoft's [voice-agent samples](https://github.com/microsoft/voice-agent).
- The agent's own instructions, model, voice and tools apply unchanged.
- The backend adds one **system message** per session, with the current customer's facts, today's date and guardrails.
- No API key is used, and no credential is ever sent to the browser.

## Configure transcript storage (Azure Blob, Entra ID)

1. **Storage account.** Use an existing account or create one. Recommended settings:
   - **Allow Blob anonymous access:** disabled.
   - **Allow storage account key access:** disabled, so only Entra ID works.
   - **Minimum TLS:** 1.2.

   Data is encrypted at rest by default with Microsoft-managed keys.
2. **Role.** Give your identity **Storage Blob Data Contributor** on the account. In Azure, give the app's managed identity the same role, scoped to the `call-transcripts` container.

   ```powershell
   az role assignment create --assignee <your-user-or-identity-object-id> `
     --role "Storage Blob Data Contributor" `
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<account>
   ```
3. **Configure.** In `.env`, set `AZURE_STORAGE_ACCOUNT_URL=https://<account>.blob.core.windows.net`. The container defaults to `call-transcripts`.
4. **Create and verify** the private container (role assignments can take a few minutes to apply):

   ```powershell
   cd backend
   python -m scripts.setup_blob
   ```

   The script:
   - signs in;
   - creates the container if it's missing, with no public access;
   - fails if the container allows anonymous access;
   - writes, reads back and deletes a probe transcript.

   Use `--check-only` to verify without creating anything.
5. **Retention (recommended).** Apply a lifecycle policy, for example [infra/blob-lifecycle-policy.json](infra/blob-lifecycle-policy.json), which moves blobs to cool after 30 days and deletes them after 90:

   ```powershell
   az storage account management-policy create --account-name <account> --resource-group <rg> `
     --policy @infra/blob-lifecycle-policy.json
   ```

Without `AZURE_STORAGE_ACCOUNT_URL`, the backend keeps transcripts in memory and logs a warning. `GET /health` reports `transcript_storage: "azure_blob"` or `"memory"`.

## Use the existing PostgreSQL database

Set `DATABASE_URL` and `DATABASE_ORGANIZATION_ID` in `.env` (see `.env.example`). The database then replaces `data/customers.json`. The schema belongs to another application's Alembic migrations, so this app only reads and writes rows and never creates or alters tables. Every query is scoped to the configured organization.

| App concept | Table | How |
|---|---|---|
| Queue | `call_tasks` | Tasks with status *queued*, ordered by `scheduled_at`. If a client has several queued tasks, only the earliest is loaded. |
| Customer | `clients` | `customer_id` = `clients.id`. A NULL `language` falls back to `DATABASE_DEFAULT_LANGUAGE`. |
| Invoice | `invoices` | The client's open invoice (status in `DATABASE_INVOICE_STATUS_MAP`) that is due first. `invoice_number` = `external_invoice_id`. Currency comes from `organizations.settings->>'currency'`, else `DATABASE_DEFAULT_CURRENCY`. Clients with no open invoice are skipped, with a warning in the log. |
| Session | `calls` | `calls.id` = session id. `provider_call_id` is NULL because this is a browser session. `duration` is in seconds. |
| Lifecycle | `call_events` | `session_started` (holds the `call_task_id`), `session_ended` (holds the blob name when Blob is configured), and `session_interrupted`. |
| Outcome | `call_actions` | `action_type = record_outcome`. `result` holds the outcome, promised date, summary and review flags. `action_status` is left at its column default. |
| Transcript | `call_transcripts` | One row per turn. |
| Audit | `audit_logs` | One row per session start and end. |

How sessions move through the tables:

- **Start Agent** sets the task to *in progress* and inserts the `calls` row in one transaction. If the task changed outside this app since the queue was loaded, the start is refused with a 503 and the queue doesn't move.
- **End Session** uploads the JSON to Blob first (if configured), then commits the database transaction. A completed session sets the task to *completed*. A failed session puts it back to *queued*, so the customer is retried. If the save fails, the session stays open and **Retry save** is safe.
- **On startup**, sessions this app left in progress (for example after a restart mid-call) are marked failed and their tasks re-queued. This assumes a single backend instance.
- **The queue is loaded at startup.** Restart the backend to pick up newly queued tasks. **Restart queue** is disabled in this mode.

Default status values (each one can be changed with a `DATABASE_*_STATUS_*` setting to match the owning application):

| Table | Values |
|---|---|
| `invoices` (read only) | `pending`, `overdue` and `partially_paid` count as still owed. |
| `call_tasks` | `queued` → `in_progress` → `completed`. A failed session puts the task back to `queued`. |
| `calls` | `initiated` while the session runs, then `completed`. `declined` means the customer asked to stop (`stop_requested`). `failed` means a technical failure. `busy` is reserved for telephony and isn't used in browser sessions. |
 The database user needs SELECT on `organizations`, `clients`, `invoices`, `call_tasks` and `calls`; INSERT/UPDATE on `call_tasks`, `calls`, `call_events`, `call_actions`, `call_transcripts` and `audit_logs`; and DELETE on `call_events`, `call_actions` and `call_transcripts`. DELETE is needed so a retried save replaces the rows it wrote earlier instead of duplicating them.

`tests/test_database_integration.py` runs the SQL against a real server when `TEST_DATABASE_URL` points at a **scratch** database. It creates the schema in a throwaway Postgres schema and drops it afterwards.

## Run locally

Backend (terminal 1):

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:create_app --factory --reload --port 8000
```

On Windows, PostgreSQL's async driver needs the selector event loop: `--reload` already uses it; without `--reload` add `--loop asyncio:SelectorEventLoop`.

API docs are at http://localhost:8000/docs. `GET /health` reports whether the agent is configured.
Without the agent settings the queue still works, and chat returns 503.

Frontend (terminal 2):

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. Use **localhost**: browsers allow the microphone only on `localhost` or https. A headset avoids echo.

To run a demo:
1. Press **Start Agent**. The browser asks for microphone permission, then the agent greets the current customer through your speakers.
2. Speak as the customer. Your speech and the agent's replies appear in the live transcript. Talking over the agent interrupts it.
3. Watch for **Detected outcome**. You can also type a reply at any time, and use **Mute** to pause the microphone.
4. Press **End Session**.
   - The microphone is released, the transcript is saved to Blob, and the dashboard moves to the next customer.
   - The **Last session** card shows the blob path, and **View saved transcript** reads it back from storage.
   - If the save fails, the customer stays current and the button becomes **Retry save**.

If microphone permission is denied, the dashboard explains how to allow it and offers **Continue with typed chat**.

## Tests

```powershell
cd backend;  pytest
cd frontend; npm test; npm run lint; npm run typecheck
```

## User authentication

The login and registration page uses the backend endpoints `POST /api/auth/register` and `POST /api/auth/login`.
Registration accepts a name, email and a password of at least 12 characters. Passwords are stored as salted
PBKDF2-HMAC-SHA256 hashes; plaintext passwords are never saved. Both endpoints return a one-hour HS256 JWT
and set the token as an HttpOnly, SameSite=Lax cookie. API clients can instead send
`Authorization: Bearer <access_token>`. `GET /api/auth/session` checks the browser session, and
`POST /api/auth/logout` revokes the current token and clears the cookie. Protected APIs and voice WebSockets
reject missing, expired or revoked tokens. Authentication requests have SQLite-backed rate limits, and
cookie-authenticated writes check the configured allowed origins.

Accounts are stored in `~/.voice-agent/users.sqlite3` (Windows: under the current user's home directory).
Set the process environment variable `AUTH_DATABASE_PATH` to use persistent deployment storage. The JWT key
is stored persistently in that SQLite database by default; deployments can instead provide a random
`AUTH_JWT_SECRET` of at least 32 characters through their secret manager. Rotating that key invalidates
existing tokens. Neither setting requires editing `.env`.

Registration is intentionally open. All registered users access the same shared dashboard data; email
verification, password recovery, MFA, roles and per-user data isolation are not implemented. Add those
controls before exposing registration to untrusted users or using real customer data.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Register with name, email and password; returns an access token and sets the session cookie |
| POST | `/api/auth/login` | Authenticate with email and password; returns an access token and sets the session cookie |
| GET | `/api/auth/session` | Check whether the current browser session is valid |
| POST | `/api/auth/logout` | Revoke the current token and clear the browser session cookie |
| GET | `/api/health` | Readiness: `agent_configured`, `transcript_storage` (also at `/health`) |
| GET | `/api/customers` | Validated customer list, in file order |
| GET | `/api/queue` | Current position: `position`, `total`, `remaining`, `current_customer`, `active_session_id` |
| POST | `/api/sessions` | Start a session for the **current** customer. Optional `expected_customer_id` returns 409 if the UI is stale |
| POST | `/api/sessions/{id}/messages` | Typed turn: `{"text": "..."}`. Send `{"text": null}` to get the agent's opening line. Returns the agent turn, the full transcript, and the validated `proposed_outcome` |
| WS | `/api/sessions/{id}/voice` | Browser voice (see below). The browser connects to FastAPI directly; only origins in `ALLOWED_ORIGINS` are accepted |
| POST | `/api/sessions/{id}/transcript` | Finalize: save the transcript to Blob, then advance. Idempotent; also closes the live agent connection. Returns 502 if the save fails (session stays open) and 409 if a save is already in progress |
| GET | `/api/sessions/{id}/transcript` | Read a finalized session's saved transcript back from storage, by session id only |
| GET | `/api/sessions` | Session history (in memory) |
| POST | `/api/queue/reset` | Demo helper: restart from customer 1 |

## Browser voice (M3)

```text
Browser mic ─► AudioWorklet ─► resample to 24 kHz PCM16 ─► WebSocket ─► FastAPI ─► input_audio_buffer.append ─► Foundry voice agent
Speakers ◄─ Web Audio player ◄─ PCM16 24 kHz ◄─ WebSocket ◄─ FastAPI ◄─ response.output_audio.delta ◄─┘
```

- **Audio format.** PCM16 little-endian, mono, 24 kHz in both directions, the same format as the Foundry voice-agent sample. The browser captures at its native rate and resamples, and plays agent audio through Web Audio.
- **Turn detection.** Handled by the agent's own server VAD as configured in Foundry. When the customer starts speaking (`input_audio_buffer.speech_started`), the browser drops any queued agent audio (barge-in).
- **Transcripts** are captured on the server:
  - The customer's turns come from `conversation.item.input_audio_transcription.completed`. Input transcription must be enabled on the agent.
  - The agent's turns come from `response.output_audio_transcript.done`.
  - User turns are placed at the moment the customer stopped speaking, so a late transcription doesn't land after the agent's reply.
  - The backend pushes a transcript snapshot to the browser on every change.
- **Failures:**
  - **Microphone denied or missing:** clear message; no session is created and the queue is untouched.
  - **Voice connection fails or the agent disconnects:** the partial transcript is kept. Typed replies still work, and **End Session** saves the session as `failed`, so the customer stays current and is retried, never skipped.
- **Security.**
  - The WebSocket rejects browser origins that aren't in `ALLOWED_ORIGINS`.
  - Dashboard API requests and voice WebSockets require a valid JWT session; bearer tokens are also supported for API clients.
  - No audio is recorded or stored, only the text transcript.

WebSocket messages, server to browser:
- binary frames: agent audio;
- JSON `ready` and `transcript` (each with `transcript` and `proposed_outcome`);
- `speech_started`, `speech_stopped`, `response_done`;
- `session_ended`, `agent_closed`, `error`.

Browser to server: binary microphone audio and `{"type": "stop"}`.

## Agent behaviour and outcome validation

- **Live session.** Each app session gets one live agent connection.
  - **Start Agent** opens it and the agent speaks first. Voice and typed turns share it, and **End Session** closes it.
  - If the connection drops, the next voice connect or typed message reconnects, and the session context includes the conversation so far.
- **Grounding.** The session context is built on the server from the current JSON record only: name, invoice, amount, due date, status and language. The browser never supplies customer facts.
- **Rules.** The context adds guardrails on top of the agent's own instructions: identify as automated; never ask for OTP, UPI PIN, CVV, card numbers, passwords or bank credentials; do not threaten or invent facts; keep replies short.
- **Recording an outcome.** The agent records an outcome by calling the `record_outcome` tool. The backend validates every call:
  - The outcome must be one of the 7 business outcomes. `technical_failure` is reserved for the system.
  - `promised_payment_date` is allowed only with `payment_promised`. It must be a real `YYYY-MM-DD` date, no earlier than today (in `BUSINESS_TIMEZONE`), and no more than 90 days ahead.

  Invalid calls are returned to the model as errors so it can ask the customer again. Invalid values are never stored.
- **Which outcome is saved at finalization:** an outcome the operator sends explicitly first, then the model's latest validated proposal, then `completed_no_commitment`. A `failed` session is always saved as `technical_failure`.
- **Transcript.** It is held server-side. An agent error or timeout returns 502 and keeps the session open; the customer's message is kept in the transcript.

## Transcript storage (M4)

- **Blob path:** `{yyyy}/{mm}/{customer_id}/{session_id}/transcript.json` in the private `call-transcripts` container.
  - The date is the session start, in UTC.
  - The blob has content type `application/json` and metadata `schema_version`, `session_status` and `outcome`.
- **Document:** schema version `1.0` from the plan: session and customer ids, start and end times, status, outcome, promised date, every turn (`turn`, `speaker`, `text`, `timestamp`) and the summary. Text only; no audio is stored.
- **Saving:**
  - **End Session** claims the session, uploads, then commits. The upload runs outside the session lock and off the event loop, so voice relaying isn't blocked.
  - New turns and a second End Session are refused while the upload is in flight.
  - The SDK retries up to 3 times. If it still fails, the API returns 502, the session stays open, the queue doesn't move, and the operator presses **Retry save**.
  - Uploads overwrite, so a retry after a lost response is harmless.
- **Reference:** the session record keeps `transcript_ref` (the blob name) and `transcript_store` (`azure_blob`).
  - This metadata is in memory. Blobs persist across restarts, but the in-app list of sessions doesn't; PostgreSQL is the optional next step.
- **Access:**
  - The container is private and is never exposed as a URL. The browser reads transcripts only through `GET /api/sessions/{id}/transcript`, which looks up the blob name from the backend's own session record. Client-supplied paths aren't accepted, and names are checked against the transcript layout.
  - `customer_id` values are restricted to `A–Z a–z 0–9 _ -`, so they're safe in paths.
  - Self-registration is open and accounts share the same dashboard data. Add user authorization and data isolation before exposing the app to untrusted users.

## Validation and safety (M5)

**Review flags.** Every agent turn is screened, and anything suspicious is shown to the operator as **Review needed**, saved in the transcript's `review_flags`, and counted on the session record. The screening looks for:
- requests for OTP, PIN, CVV, card numbers or passwords;
- amounts, invoice numbers or dates that aren't in the customer record and that the customer didn't say;
- mentions of fees, penalties, legal action or credit scores.

Flags are heuristics over transcript text. They support human review but aren't a guarantee: numbers spoken as words aren't checked.

**Redaction.**
- Card numbers (Luhn-checked) and codes the customer gives after "OTP / PIN / CVV / password" are replaced with `[removed]` before the transcript is stored.
- Typed secrets are also removed before they reach the agent.

**Scenario evaluation against your real agent.**

```powershell
cd backend
python -m scripts.evaluate_agent --list           # 12 scenarios
python -m scripts.evaluate_agent                  # all; or -s stop -s dispute, or --tag safety
```

- **Outcome scenarios:** reported paid, promise with date ("this Friday" → that date), promise without date, callback, dispute, human, stop, no commitment.
- **Safety scenarios:** card and CVV bait, OTP shared, fee and balance probe, prompt injection asking for other customers' data.

Each scenario runs through the app's own conversation path: the same context, tool validation, redaction and screening. The runner checks:
- the outcome and promised date;
- no credential requests or pressure;
- no unverified facts;
- secrets redacted;
- no other customer's data.

It prints the transcripts, writes a JSON report to `backend/reports/` (git-ignored, because it contains transcripts) and exits with code 1 if any scenario fails. It calls your agent for real, so normal usage costs apply.

**Demo.** See [docs/DEMO.md](docs/DEMO.md).
- The dashboard shows readiness chips (agent configured; transcripts in Azure Blob or in memory), a collapsible **How to run the demo** guide, a timer, live review flags, and the session duration and flag count on the last session.
- The agent's opening now says the conversation is transcribed.

**Privacy.** See [docs/PRIVACY_REVIEW.md](docs/PRIVACY_REVIEW.md) for the data inventory, the controls in place, and the gaps and sign-off needed before using real customer data. The main gaps are operator authorization, the consent approach, retention, residency and collections conduct rules.

## Sequencing rules

- Records are processed strictly in file order.
- Only one session can be active at a time.
- The queue advances by exactly one record, and only after the transcript is saved. If the save fails, it returns 502, the session stays open, and the operator can retry.
- A session finalized as `failed` releases the slot but keeps the same customer current, so the record is retried and never skipped.

Session and queue state is held in memory, so restarting the backend starts again at customer 1.
