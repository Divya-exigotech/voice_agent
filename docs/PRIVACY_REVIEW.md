# Privacy, consent and retention review

**Scope:** the browser-based payment follow-up PoC (M1–M5). It uses synthetic data and places no telephone calls.

This document records what the PoC does today and what must be decided before any **real customer data** is used. It isn't legal advice: review the open items with your legal, compliance and security teams.

## 1. Data inventory

| Data | Source | Where it goes | Stored? |
|---|---|---|---|
| Customer name, phone number, preferred language, invoice facts | `data/customers.json` | Backend memory. Name, language and invoice facts go to the Foundry agent as session context. **The phone number isn't sent to the agent** | The file only |
| Microphone audio | Operator's browser | Backend WebSocket → Foundry voice agent (streamed) | **No.** Audio is never written to disk or Blob |
| Agent audio | Foundry voice agent | Backend → browser (streamed) | **No** |
| Transcript text (both speakers) | Foundry transcription events | Backend memory → Azure Blob at session end | **Yes:** `call-transcripts/{yyyy}/{mm}/{customer_id}/{session_id}/transcript.json` |
| Outcome, promised date, summary, review flags | Agent tool call (validated) or operator | Saved in the transcript document | Yes, with the transcript |
| Session metadata | Backend | Memory only | Lost on restart |
| Evaluation reports | `scripts.evaluate_agent` | `backend/reports/` (git-ignored) | Local files. Delete them when done |

Data that the Foundry service itself processes, such as audio, transcription and model inputs/outputs, is handled under the Microsoft Foundry and Azure OpenAI data, privacy and security terms for your subscription. Confirm the region and any abuse-monitoring retention that applies to your resource.

## 2. Controls in place

**Disclosure.** The agent is told to say it's an automated assistant and that the conversation is transcribed as text, in its opening line (`backend/app/services/agent_prompt.py`).

**No payment credentials.**
- The agent is instructed never to ask for or accept OTP, UPI PIN, CVV, card numbers, passwords or bank credentials.
- Agent turns are screened, and suspected requests raise a **review flag**.
- The `credential_bait` and `otp_shared` evaluation scenarios test this.

**Redaction.**
- Card numbers (Luhn-checked) and codes the customer says or types after "OTP / PIN / CVV / password" are replaced with `[removed]` before the transcript is stored.
- Typed secrets are removed before they reach the agent. Spoken audio has already reached the agent by then, so only the transcript is protected.

**Grounding.** Agent turns mentioning amounts, invoice numbers or dates not in the record (or said by the customer) are flagged, as are mentions of fees, penalties or legal action.

**Minimisation.**
- Text transcripts only; no audio recordings.
- The phone number isn't used.
- Only the current customer's facts are sent to the agent.

**Storage security.**
- The Blob container is private and never exposed by URL; the setup script fails if anonymous access is enabled.
- Access uses Microsoft Entra ID with RBAC (Storage Blob Data Contributor), with no keys or SAS.
- Data is encrypted at rest by default with Microsoft-managed keys.

**Retention.** An example lifecycle policy moves transcripts to cool storage after 30 days and deletes them after 90 days (`infra/blob-lifecycle-policy.json`). **It isn't applied automatically.**

**Logging.** Server logs record session IDs, blob names, error messages and review-flag kinds, but not transcript text or flag excerpts.

**Secrets.**
- `.env` is git-ignored.
- No credentials reach the browser bundle.
- The browser reaches FastAPI via a same-origin proxy (REST) or an origin-checked WebSocket.

## 3. Gaps and decisions needed before real data

| # | Item | Why it matters | Suggested action |
|---|---|---|---|
| 1 | **No operator authentication** | Anyone who can reach the app can start sessions and read transcripts by session ID | Put the frontend and API behind Microsoft Entra ID sign-in, for example App Service authentication, and authorise operators by group |
| 2 | **Lawful basis and notice** | Real customers' personal data and conversations are processed by an AI service | Confirm the lawful basis and the notice wording with legal. For customers in India, review obligations under the Digital Personal Data Protection Act, 2023 |
| 3 | **Consent to AI interaction and transcription** | The agent discloses this, but the PoC doesn't capture an explicit opt-in or opt-out | Decide whether explicit consent is required. If so, add a consent step, store the consent result with the transcript, and end the session if the customer declines |
| 4 | **Collections conduct rules** | Payment follow-up may fall under sector rules on fair practices, contact hours and frequency (for example RBI guidance if the organisation is regulated) | Map the applicable rules into the agent instructions and evaluation scenarios before going live |
| 5 | **Retention period** | The 90-day example is a placeholder | Set the retention period with legal and records management, apply the lifecycle policy, and consider immutability (legal hold) where disputes are recorded |
| 6 | **Data residency** | The Foundry project and storage account regions determine where data is processed and stored | Confirm both regions meet residency requirements |
| 7 | **Redaction limits** | Rules are pattern-based. They miss numbers spoken as words and unusual phrasings; spoken secrets still reach the agent | Treat the flags as monitoring, not a guarantee. Consider a PII detection service (for example Azure AI Language PII) on transcripts before storage |
| 8 | **Data subject rights** | Customers may ask for access to, or deletion of, their transcripts | Document how to find a customer's transcripts (blob prefix `{yyyy}/{mm}/{customer_id}/`) and delete them; keep session metadata in a database |
| 9 | **Session metadata persistence** | In-memory metadata is lost on restart; the blobs remain | Add PostgreSQL (or similar) for session records and the audit trail |
| 10 | **Access review and monitoring** | No alerting on unusual access | Enable storage diagnostic logs and Defender for Storage; review role assignments periodically |
| 11 | **Telephony (M6)** | Real outbound calls add call-recording, caller-ID and telemarketing rules (for example TRAI regulations in India) | Out of scope until M6; include in the telephony evaluation |

## 4. Sign-off before using real customer data

- [ ] Operator authentication enabled (gap 1)
- [ ] Lawful basis, notice and consent approach approved (gaps 2–3)
- [ ] Collections conduct rules reflected in the agent instructions and tests (gap 4)
- [ ] Retention period set and lifecycle policy applied (gap 5)
- [ ] Regions confirmed (gap 6)
- [ ] `python -m scripts.evaluate_agent` passes on the final agent configuration, and the report has been reviewed
- [ ] Synthetic `data/customers.json` replaced through an approved data-handling process
