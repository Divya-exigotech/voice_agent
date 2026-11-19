import { formatDate, humanize } from "@/lib/format";
import type { OutcomeProposal, ReviewFlag, UiSessionStatus } from "@/lib/types";

import { ReviewFlags } from "./ReviewFlags";
import { SessionTimer } from "./SessionTimer";

const STATUS_LABEL: Record<UiSessionStatus, string> = {
  idle: "Ready",
  connecting: "Connecting…",
  active: "Active",
  ending: "Saving transcript…",
  completed: "Completed",
  failed: "Failed",
};

export type SessionMode = "voice" | "text" | null;

export interface AudioState {
  muted: boolean;
  userSpeaking: boolean;
  agentSpeaking: boolean;
}

interface Props {
  status: UiSessionStatus;
  mode: SessionMode;
  audio: AudioState;
  canStart: boolean;
  canEnd: boolean;
  endLabel?: string;
  offerTypedFallback: boolean;
  queueCompleted: boolean;
  proposal: OutcomeProposal | null;
  flags?: ReviewFlag[];
  startedAt?: string | null;
  error: string | null;
  onStart: () => void;
  onStartTyped: () => void;
  onEnd: () => void;
  onToggleMute: () => void;
}

function micLabel(status: UiSessionStatus, mode: SessionMode, audio: AudioState): string {
  if (mode === "text") return "Off (typed conversation)";
  if (mode !== "voice" || status !== "active") return "Off";
  if (audio.muted) return "Muted";
  if (audio.userSpeaking) return "Customer speaking…";
  if (audio.agentSpeaking) return "Agent speaking…";
  return "Listening";
}

export function VoiceSessionPanel({
  status,
  mode,
  audio,
  canStart,
  canEnd,
  endLabel = "End Session",
  offerTypedFallback,
  queueCompleted,
  proposal,
  flags = [],
  startedAt = null,
  error,
  onStart,
  onStartTyped,
  onEnd,
  onToggleMute,
}: Props) {
  const voiceLive = mode === "voice" && status === "active";
  return (
    <section className="card session" aria-label="Agent session">
      <div className="session-header">
        <p className="eyebrow">Agent session</p>
        <span className={`status status-${status}`} data-testid="session-status">
          {STATUS_LABEL[status]}
        </span>
      </div>

      <div className="session-actions">
        <button type="button" className="btn btn-primary" onClick={onStart} disabled={!canStart}>
          Start Agent
        </button>
        <button type="button" className="btn btn-danger" onClick={onEnd} disabled={!canEnd}>
          {endLabel}
        </button>
        {voiceLive && (
          <button type="button" className="btn" onClick={onToggleMute} aria-pressed={audio.muted}>
            {audio.muted ? "Unmute" : "Mute"}
          </button>
        )}
      </div>

      {startedAt && <SessionTimer startedAt={startedAt} />}

      <p className="mic-state small" data-testid="mic-state">
        <span className={`mic-dot ${voiceLive && !audio.muted ? "mic-dot-live" : ""}`} aria-hidden />
        Microphone: {micLabel(status, mode, audio)}
      </p>

      {proposal && (
        <p className="proposal" data-testid="proposed-outcome">
          Detected outcome: <strong>{humanize(proposal.outcome)}</strong>
          {proposal.promised_payment_date && ` · ${formatDate(proposal.promised_payment_date)}`}
        </p>
      )}

      <ReviewFlags flags={flags} />

      {queueCompleted && <p className="notice">All customers completed.</p>}
      {error && (
        <div className="error" role="alert">
          <p>{error}</p>
          {offerTypedFallback && (
            <button type="button" className="btn btn-small" onClick={onStartTyped} disabled={!canStart}>
              Continue with typed chat
            </button>
          )}
        </div>
      )}
    </section>
  );
}
