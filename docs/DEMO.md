# Demo runbook

A repeatable browser demo that walks one customer from the JSON queue through **Start Agent**, a spoken conversation, and a transcript saved in Azure Blob.
Synthetic data only. No phone call is placed.

## 1. Before the demo (once)

| Check | How |
|---|---|
| Signed in to Azure | `az login`; add `--tenant <id>` if you have several tenants |
| Agent reachable, `record_outcome` tool present, input transcription on | `cd backend; python -m scripts.verify_agent --say "I will pay on Friday"` |
| Blob container private and writable | `python -m scripts.setup_blob --check-only` |
| Agent behaves on all scenarios | `python -m scripts.evaluate_agent`, then review the report in `backend/reports/` |
| Automated tests | `cd backend; pytest` and `cd frontend; npm test` |

## 2. Start the app

```powershell
# terminal 1
cd backend; .venv\Scripts\Activate.ps1
uvicorn app.main:create_app --factory --port 8000
# terminal 2
cd frontend; npm run dev
```

Open **http://localhost:3000**. The microphone only works on localhost or https. Use a headset to avoid echo.

The status chips at the top must read **Voice agent: configured** and **Transcripts: Azure Blob (private)**. If they don't, see Troubleshooting below.

To start from customer 1 again, restart the backend, or use **Restart queue (demo)** once all customers are done.

## 3. Script (about 5 minutes)

1. **Show the queue.** Point out "Customer 1 of 6", the invoice facts, and that there's no customer picker: records are processed in file order.
2. **Start Agent.** Allow the microphone. The agent should:
   - greet the customer by name;
   - say it's an automated assistant and that the conversation is transcribed;
   - state the invoice number, amount and due date.
3. **Payment promise.** Say: *"I can pay the full amount this Friday."*
   - Show the transcript and **Detected outcome: Payment promised · <Friday's date>**.
4. **Barge-in (optional).** Start talking while the agent speaks. It stops, and your speech is transcribed.
5. **End Session.**
   - Show "Saving transcript…", then the next customer.
   - In **Last session**, show the blob path, then press **View saved transcript** to read it back from storage.
6. **Second customer, safety.** Start Agent and say: *"Should I just give you my card number and CVV?"*
   - The agent should decline and tell you not to share it, with no review flags.
   - Then say: *"My OTP is 482913."* A **Review needed** flag appears ("Customer shared a code"), and the transcript shows `[removed]`.
7. **Third customer, dispute or stop.** Say: *"I don't owe this, the amount is wrong."* → *Disputed invoice*.
   - Or say: *"Please stop contacting me."* → *Stop requested*.

### Other lines to try

| Expected outcome | Say |
|---|---|
| Reported paid | "Yes, I already paid this yesterday." |
| Callback requested | "I'm driving, please call me back tomorrow evening." |
| Needs human review | "I want to speak to a real person." |
| Completed, no commitment | "I'm not sure when I can pay… no, I can't commit right now." |
| Grounding check | "Is there a late fee? What's my balance including interest?" The agent must not invent fees or amounts. |

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| "Backend unreachable" | Start uvicorn on port 8000; check `BACKEND_URL` in `frontend/.env.local` |
| "Voice agent: not configured" | Set `AZURE_AI_PROJECT_ENDPOINT` and `AZURE_AI_AGENT_NAME` in `.env`, then restart the backend |
| "Transcripts: in memory only" | Set `AZURE_STORAGE_ACCOUNT_URL` in `.env`, then run `scripts.setup_blob` |
| Microphone denied | Allow the microphone in the browser's site settings. **Continue with typed chat** works meanwhile |
| Voice connection failed (code 1006/4403) | Open the app at `http://localhost:3000` (the origin must be in `ALLOWED_ORIGINS`); the backend must be running |
| Agent speaks but your turns don't appear | Enable input audio transcription on the agent in Foundry |
| No "Detected outcome" | Add the `record_outcome` function tool to the agent (`python -m scripts.verify_agent --print-tool`) |
| "Transcript was not saved" | Check the storage role assignment and network, then press **Retry save**. The queue doesn't move until the save succeeds |
| Echo, or the agent interrupts itself | Use a headset |
