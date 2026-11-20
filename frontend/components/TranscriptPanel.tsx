import type { ReactNode } from "react";

import { formatTime } from "@/lib/format";
import type { TranscriptTurn } from "@/lib/types";

interface Props {
  turns: TranscriptTurn[];
  thinking?: boolean;
  children?: ReactNode;
}

export function TranscriptPanel({ turns, thinking = false, children }: Props) {
  return (
    <section className="card transcript-card" aria-label="Transcript">
      <p className="eyebrow">Live transcript</p>
      {turns.length === 0 && !thinking ? (
        <p className="muted small">No turns yet. Press Start Agent to begin the conversation.</p>
      ) : (
        <ol className="transcript">
          {turns.map((t, i) => (
            <li key={i} className={`turn turn-${t.speaker}`}>
              <span className="turn-meta">
                {t.speaker === "agent" ? "Agent" : "Customer"} · {formatTime(t.timestamp)}
              </span>
              <span>{t.text}</span>
            </li>
          ))}
          {thinking && (
            <li className="turn turn-agent turn-thinking" aria-live="polite">
              <span className="turn-meta">Agent</span>
              <span>Thinking…</span>
            </li>
          )}
        </ol>
      )}
      {children}
    </section>
  );
}
