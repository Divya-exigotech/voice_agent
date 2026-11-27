"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type {
  Health,
  OutcomeProposal,
  QueueState,
  ReviewFlag,
  TranscriptTurn,
  UiSessionStatus,
} from "@/lib/types";
import {
  MicrophoneError,
  requestMicrophone,
  VoiceSession,
  type TranscriptUpdate,
} from "@/lib/voice/voiceSession";

import { ChatComposer } from "./ChatComposer";
import { CurrentCustomerCard } from "./CurrentCustomerCard";
import { SessionSavedNotice, type LastSession } from "./SessionSavedNotice";
import { SequenceProgress } from "./SequenceProgress";
import { SystemStatus } from "./SystemStatus";
import { TranscriptPanel } from "./TranscriptPanel";
import { VoiceSessionPanel, type AudioState, type SessionMode } from "./VoiceSessionPanel";

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// After a payment promise is recorded, wait this long in silence (the agent may
// still be saying goodbye) before the call is ended automatically.
const AUTO_END_GRACE_MS = 4000;
// The backend queues the follow-up call after its delay; poll for it this often.
// Outcomes that end the call by themselves and start the next customer's call.
const AUTO_OUTCOMES = ["payment_promised", "reported_paid", "needs_human_review"];

const FOLLOWUP_POLL_MS = 1000;
// Stop waiting for a follow-up call after this long.
const FOLLOWUP_MAX_WAIT_MS = 10 * 60 * 1000;

const SILENT: AudioState = { muted: false, userSpeaking: false, agentSpeaking: false };

export function Dashboard() {
  const [queue, setQueue] = useState<QueueState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [status, setStatus] = useState<UiSessionStatus>("idle");
  const [mode, setMode] = useState<SessionMode>(null);
  const [audio, setAudio] = useState<AudioState>(SILENT);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [proposal, setProposal] = useState<OutcomeProposal | null>(null);
  const [flags, setFlags] = useState<ReviewFlag[]>([]);
  const [startedAt, setStartedAt] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [micProblem, setMicProblem] = useState(false);
  // The voice path failed mid-session: End Session saves it as failed (customer retried).
  const [voiceLost, setVoiceLost] = useState(false);
  // The last End Session could not save the transcript; the button becomes "Retry save".
  const [saveFailed, setSaveFailed] = useState(false);
  const [last, setLast] = useState<LastSession | null>(null);
  // A payment promise was recorded: that customer is called again, but only after
  // every other queued customer, once the backend adds the follow-up to the queue.
  const [followUpsPending, setFollowUpsPending] = useState(false);
  const [autoNext, setAutoNext] = useState(false);
  // Guards against duplicate End Session events while a finalize is in flight.
  const finalizing = useRef(false);
  // The session agent replies belong to; late events for an ended session are ignored.
  const currentSession = useRef<string | null>(null);
  const voice = useRef<VoiceSession | null>(null);
  // Always the latest handlers, for timers that outlive a render.
  const endHandler = useRef<() => Promise<void>>(async () => {});
  const startHandler = useRef<() => Promise<void>>(async () => {});

  const applyQueue = useCallback((q: QueueState) => {
    setQueue(q);
    setLoadError(null);
    // Resume a session left open (e.g. after a page reload) so it can be ended.
    if (q.active_session_id) {
      currentSession.current = q.active_session_id;
      setSessionId(q.active_session_id);
      setMode("text");
      setStatus("active");
    }
  }, []);

  const loadQueue = useCallback(async () => {
    try {
      applyQueue(await api.getQueue());
    } catch (err) {
      setLoadError(message(err));
    }
  }, [applyQueue]);

  useEffect(() => {
    let cancelled = false;
    api.getQueue().then(
      (q) => !cancelled && applyQueue(q),
      (err) => !cancelled && setLoadError(message(err)),
    );
    return () => {
      cancelled = true;
      void voice.current?.stop(); // release the microphone on unmount
    };
  }, [applyQueue]);

  useEffect(() => {
    let cancelled = false;
    api.getHealth().then(
      (h) => !cancelled && setHealth(h),
      (err) => !cancelled && setHealthError(message(err)),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const busy = status === "connecting" || status === "ending";
  const customer = queue?.current_customer ?? null;
  const canStart = !!customer && !queue?.completed && sessionId === null && !busy;
  const canEnd = sessionId !== null && !busy;
  const showTranscript = sessionId !== null || busy;
  const canChat = sessionId !== null && !busy;

  const outcomeRecorded =
    !!proposal &&
    (proposal.end_call === true ||
      (AUTO_OUTCOMES.includes(proposal.outcome) &&
        (proposal.outcome !== "payment_promised" || !!proposal.promised_payment_date)));

  useEffect(() => {
    endHandler.current = handleEnd;
    startHandler.current = handleStart;
  });

  // End the voice call by itself once a payment date is recorded and the
  // conversation has gone quiet (the customer and the agent both finished).
  useEffect(() => {
    if (!outcomeRecorded || mode !== "voice" || status !== "active") return;
    if (audio.userSpeaking || audio.agentSpeaking) return;
    const timer = setTimeout(() => void endHandler.current(), AUTO_END_GRACE_MS);
    return () => clearTimeout(timer);
  }, [outcomeRecorded, mode, status, audio.userSpeaking, audio.agentSpeaking]);

  // Every other customer is done: wait for the backend to queue the follow-up calls.
  const waitingForFollowUps =
    followUpsPending && !!queue?.completed && sessionId === null && !busy;
  useEffect(() => {
    if (!waitingForFollowUps) return;
    let cancelled = false;
    let inFlight = false;
    const deadline = Date.now() + FOLLOWUP_MAX_WAIT_MS;
    const timer = setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const q = await api.getQueue();
        if (cancelled) return;
        if (q.current_customer && !q.active_session_id) {
          setQueue(q);
          setAutoNext(true);
        } else if (Date.now() > deadline) {
          setFollowUpsPending(false);
        }
      } catch {
        // keep polling; the next tick retries
      } finally {
        inFlight = false;
      }
    }, FOLLOWUP_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [waitingForFollowUps]);

  // Start the follow-up call as soon as the queue shows it and nothing is running.
  useEffect(() => {
    if (!autoNext || !canStart) return;
    void startHandler.current(); // resetForNewSession clears autoNext
  }, [autoNext, canStart]);

  function applyTranscript(sid: string, update: TranscriptUpdate) {
    if (currentSession.current !== sid) return;
    setTurns(update.transcript);
    setProposal(update.proposed_outcome);
    setFlags(update.review_flags ?? []);
  }

  async function askAgent(sid: string, text: string | null) {
    setThinking(true);
    setError(null);
    if (text !== null) {
      // Show the typed reply immediately; the server transcript replaces it.
      setTurns((prev) => [...prev, { speaker: "user", text, timestamp: new Date().toISOString() }]);
    }
    try {
      const res = await api.sendMessage(sid, text);
      applyTranscript(sid, res);
    } catch (err) {
      if (currentSession.current !== sid) return;
      setError(`Agent did not respond: ${message(err)}`);
    } finally {
      if (currentSession.current === sid) setThinking(false);
    }
  }

  /** Create the backend session for the current customer; null if it failed. */
  async function createSession(): Promise<string | null> {
    if (!customer) return null;
    try {
      // The backend binds the session to the current queue record; we pass the
      // customer we are displaying so a stale UI cannot start the wrong one.
      const created = await api.startSession(customer.customer_id);
      const sid = created.session_id;
      currentSession.current = sid;
      setSessionId(sid);
      setStartedAt(created.started_at);
      return sid;
    } catch (err) {
      setStatus("failed");
      setError(`Could not start session: ${message(err)}`);
      if (err instanceof ApiError && err.status === 409) void loadQueue();
      return null;
    }
  }

  function resetForNewSession() {
    setAutoNext(false);
    setError(null);
    setMicProblem(false);
    setVoiceLost(false);
    setSaveFailed(false);
    setTurns([]);
    setProposal(null);
    setFlags([]);
    setStartedAt(null);
    setAudio(SILENT);
  }

  async function handleStart() {
    if (!customer || !canStart) return;
    resetForNewSession();
    setStatus("connecting");

    // Microphone permission is requested only now, after Start Agent.
    let stream: MediaStream;
    try {
      stream = await requestMicrophone();
    } catch (err) {
      setStatus("failed");
      setMicProblem(true);
      setError(err instanceof MicrophoneError ? err.message : `Microphone error: ${message(err)}`);
      return;
    }

    const sid = await createSession();
    if (!sid) {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    setMode("voice");

    const session = new VoiceSession(stream, {
      onTranscript: (update) => applyTranscript(sid, update),
      onUserSpeaking: (speaking) =>
        currentSession.current === sid && setAudio((a) => ({ ...a, userSpeaking: speaking })),
      onAgentSpeaking: (speaking) =>
        currentSession.current === sid && setAudio((a) => ({ ...a, agentSpeaking: speaking })),
      onError: (msg) => {
        if (currentSession.current !== sid) return;
        setVoiceLost(true);
        setStatus("failed");
        setAudio(SILENT);
        setError(`${msg}. You can type replies, or press End Session to save the transcript as failed.`);
      },
    });
    voice.current = session;
    try {
      await session.connect(sid);
      if (currentSession.current === sid) setStatus("active");
    } catch (err) {
      await session.stop();
      if (currentSession.current !== sid) return;
      setVoiceLost(true);
      setStatus("failed");
      setError(
        `Voice connection failed: ${message(err)}. You can type replies, or press End Session to save the session as failed.`,
      );
    }
  }

  async function handleStartTyped() {
    if (!customer || !canStart) return;
    resetForNewSession();
    setStatus("connecting");
    const sid = await createSession();
    if (!sid) return;
    setMode("text");
    setStatus("active");
    await askAgent(sid, null); // agent speaks first
  }

  function handleToggleMute() {
    const muted = !audio.muted;
    voice.current?.setMuted(muted);
    setAudio((a) => ({ ...a, muted }));
  }

  async function handleEnd() {
    if (!sessionId || !queue || finalizing.current) return;
    finalizing.current = true;
    const endedCustomer = customer;
    const wasVoice = mode === "voice";
    const sessionStatus = voiceLost ? "failed" : "completed";
    setStatus("ending");
    setError(null);
    await voice.current?.stop();
    voice.current = null;
    setAudio(SILENT);
    try {
      // The backend holds the transcript turns and the validated outcome
      // proposal, so nothing else needs to be sent.
      const result = await api.finalizeSession(sessionId, {
        session_status: sessionStatus,
        transcript: [],
      });
      currentSession.current = null;
      if (endedCustomer) setLast({ session: result.session, customer: endedCustomer });
      setQueue(result.queue);
      setSessionId(null);
      setStartedAt(null);
      setFlags([]); // now shown on the Last session card
      setMode(null);
      setThinking(false);
      setVoiceLost(false);
      setSaveFailed(false);
      setStatus(sessionStatus === "failed" ? "failed" : "completed");
      const outcome = result.session.outcome;
      if (
        wasVoice &&
        sessionStatus === "completed" &&
        outcome &&
        (AUTO_OUTCOMES.includes(outcome) || outcomeRecorded)
      ) {
        setAutoNext(true); // start the next customer's call by itself
        if (outcome === "payment_promised") setFollowUpsPending(true); // called again last
      }
      if (sessionStatus === "failed") {
        setError("Session saved as failed. The same customer stays current so it can be retried.");
      }
    } catch (err) {
      // Session is still open on the server and the queue has not moved; retry the save.
      setStatus("failed");
      setSaveFailed(true);
      setError(`The transcript was not saved: ${message(err)}. Press Retry save to try again.`);
    } finally {
      finalizing.current = false;
    }
  }

  async function handleReset() {
    try {
      setQueue(await api.resetQueue());
      setLast(null);
      resetForNewSession();
      setStatus("idle");
    } catch (err) {
      setError(`Could not restart queue: ${message(err)}`);
    }
  }

  if (loadError) {
    return (
      <div className="card error-card" role="alert">
        <p>Could not load the customer queue: {loadError}</p>
        <button type="button" className="btn" onClick={() => void loadQueue()}>
          Retry
        </button>
      </div>
    );
  }

  if (!queue) {
    return <p className="muted">Loading customer queue…</p>;
  }

  return (
    <div className="dashboard">
      <div className="agent-top">
        <SystemStatus health={health} error={healthError} />
        <SequenceProgress queue={queue} />
      </div>
      {waitingForFollowUps && (
        <p className="muted" role="status">
          All customers have been called. Customers who promised a payment date are called again
          automatically in a few minutes.
        </p>
      )}
      {last && !sessionId && <SessionSavedNotice key={last.session.session_id} last={last} />}
      <div className={`grid agent-grid${showTranscript ? " agent-live" : " agent-idle"}`}>
        <div className="column">
          {customer ? (
            <CurrentCustomerCard customer={customer} />
          ) : (
            <section className="card done-card">
              <h2>All customers completed</h2>
              {health?.customer_source === "postgres" ? (
                <p className="muted">
                  Every queued call task has been processed. Restart the backend to load newly queued tasks.
                </p>
              ) : (
                <>
                  <p className="muted">Every record in the customer file has been processed.</p>
                  <button type="button" className="btn" onClick={() => void handleReset()}>
                    Restart queue (demo)
                  </button>
                </>
              )}
            </section>
          )}
          <VoiceSessionPanel
            status={status}
            mode={mode}
            audio={audio}
            canStart={canStart}
            canEnd={canEnd}
            endLabel={saveFailed ? "Retry save" : "End Session"}
            offerTypedFallback={micProblem}
            queueCompleted={queue.completed}
            proposal={proposal}
            flags={flags}
            startedAt={sessionId ? startedAt : null}
            error={error}
            onStart={() => void handleStart()}
            onStartTyped={() => void handleStartTyped()}
            onEnd={() => void handleEnd()}
            onToggleMute={handleToggleMute}
          />
          
        </div>
        {showTranscript && (
        <div className="column">
          <TranscriptPanel turns={turns} thinking={thinking}>
            <ChatComposer
              disabled={!canChat}
              thinking={thinking}
              onSend={(text) => sessionId && void askAgent(sessionId, text)}
            />
          </TranscriptPanel>
        </div>
        )}
      </div>
    </div>
  );
}
