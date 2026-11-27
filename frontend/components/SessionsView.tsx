"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { formatDuration, formatTime, humanize } from "@/lib/format";
import type { Customer, SessionRecord, TranscriptDocument } from "@/lib/types";

import { ReviewFlags } from "./ReviewFlags";

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function SessionsView() {
  const [sessions, setSessions] = useState<SessionRecord[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SessionRecord | null>(null);
  const [doc, setDoc] = useState<TranscriptDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listSessions(), api.listCustomers()]).then(
      ([s, c]) => {
        if (cancelled) return;
        setSessions([...s].sort((a, b) => b.started_at.localeCompare(a.started_at)));
        const map: Record<string, string> = {};
        for (const cust of c.customers as Customer[]) map[cust.customer_id] = cust.customer_name;
        setNames(map);
      },
      (err) => !cancelled && setError(errMsg(err)),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  async function open(s: SessionRecord) {
    setSelected(s);
    setDoc(null);
    setDocError(null);
    if (!s.transcript_ref) return;
    setLoading(true);
    try {
      setDoc(await api.getTranscript(s.session_id));
    } catch (err) {
      setDocError(`Could not load the saved transcript: ${errMsg(err)}`);
    } finally {
      setLoading(false);
    }
  }

  if (error) {
    return (
      <div className="card error-card" role="alert">
        <p>Could not load sessions: {error}</p>
      </div>
    );
  }
  if (!sessions) return <p className="muted">Loading sessions…</p>;

  return (
    <div className="grid">
      <section className="card" aria-label="Sessions">
        <p className="eyebrow">Sessions · {sessions.length}</p>
        {sessions.length === 0 ? (
          <p className="muted small">No sessions yet. Start one from the Agent page.</p>
        ) : (
          <ul className="session-list">
            {sessions.map((s) => (
              <li key={s.session_id}>
                <button
                  type="button"
                  className={`session-row${selected?.session_id === s.session_id ? " session-row-active" : ""}`}
                  onClick={() => void open(s)}
                >
                  <span className="session-row-main">
                    <strong>{names[s.customer_id] ?? s.customer_id}</strong>
                    <span className="muted small">{when(s.started_at)}</span>
                  </span>
                  <span className="session-row-meta">
                    <span className={`status status-${s.status}`}>{humanize(s.status)}</span>
                    <span className="muted small">{s.outcome ? humanize(s.outcome) : "—"}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card" aria-label="Saved transcript">
        <p className="eyebrow">Saved transcript</p>
        {!selected ? (
          <p className="muted small">Select a session to read its transcript.</p>
        ) : (
          <>
            <h2 className="customer-name">{names[selected.customer_id] ?? selected.customer_id}</h2>
            <p className="muted small">
              {when(selected.started_at)}
              {selected.ended_at &&
                ` · ${formatDuration(new Date(selected.ended_at).getTime() - new Date(selected.started_at).getTime())}`}
            </p>
            {!selected.transcript_ref && <p className="muted small">No transcript was saved for this session.</p>}
            {loading && <p className="muted small">Loading…</p>}
            {docError && (
              <p className="error" role="alert">
                {docError}
              </p>
            )}
            {doc && (
              <div className="saved-transcript">
                {doc.transcript.length === 0 ? (
                  <p className="muted small">No conversation turns were captured.</p>
                ) : (
                  <ol className="transcript">
                    {doc.transcript.map((t, i) => (
                      <li key={t.turn ?? i} className={`turn turn-${t.speaker}`}>
                        <span className="turn-meta">
                          {t.speaker === "agent" ? "Agent" : "Customer"} · {formatTime(t.timestamp)}
                        </span>
                        <span>{t.text}</span>
                      </li>
                    ))}
                  </ol>
                )}
                {doc.summary && <p className="muted small">Summary: {doc.summary}</p>}
                <ReviewFlags flags={doc.review_flags ?? []} />
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
