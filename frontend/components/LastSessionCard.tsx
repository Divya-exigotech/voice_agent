"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate, formatDuration, formatTime, humanize } from "@/lib/format";
import type { Customer, SessionRecord, TranscriptDocument } from "@/lib/types";

import { ReviewFlags } from "./ReviewFlags";

export interface LastSession {
  session: SessionRecord;
  customer: Customer;
}

function storageLabel(session: SessionRecord): string {
  if (!session.transcript_ref) return "Not saved";
  if (session.transcript_store === "azure_blob") return "Saved to Azure Blob Storage";
  if (session.transcript_store === "postgres") return "Saved to the database";
  if (session.transcript_store === "postgres+azure_blob") return "Saved to the database and Azure Blob Storage";
  return "Saved in memory (development only, not persisted)";
}

export function LastSessionCard({ last }: { last: LastSession }) {
  const { session, customer } = last;
  const [saved, setSaved] = useState<TranscriptDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function viewTranscript() {
    setLoading(true);
    setError(null);
    try {
      setSaved(await api.getTranscript(session.session_id));
    } catch (err) {
      setError(`Could not load the saved transcript: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card" aria-label="Last session">
      <p className="eyebrow">Last session</p>
      <dl className="facts">
        <div>
          <dt>Customer</dt>
          <dd>{customer.customer_name}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{humanize(session.status)}</dd>
        </div>
        <div>
          <dt>Outcome</dt>
          <dd>{session.outcome ? humanize(session.outcome) : "—"}</dd>
        </div>
        {session.promised_payment_date && (
          <div>
            <dt>Promised date</dt>
            <dd>{formatDate(session.promised_payment_date)}</dd>
          </div>
        )}
        <div>
          <dt>Time</dt>
          <dd>
            {formatTime(session.started_at)}
            {session.ended_at ? ` – ${formatTime(session.ended_at)}` : ""}
            {session.ended_at && (
              <span className="muted">
                {" "}
                ({formatDuration(new Date(session.ended_at).getTime() - new Date(session.started_at).getTime())})
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt>Review flags</dt>
          <dd data-testid="review-flag-count">
            {session.review_flag_count ? (
              <span className="pill pill-overdue">{session.review_flag_count} to review</span>
            ) : (
              "None"
            )}
          </dd>
        </div>
        <div>
          <dt>Transcript</dt>
          <dd data-testid="transcript-storage">{storageLabel(session)}</dd>
        </div>
      </dl>

      {session.transcript_ref && (
        <p className="muted small blob-ref" title={session.transcript_ref}>
          {session.transcript_ref}
        </p>
      )}

      {session.transcript_ref && !saved && (
        <button type="button" className="btn btn-small" onClick={() => void viewTranscript()} disabled={loading}>
          {loading ? "Loading…" : "View saved transcript"}
        </button>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {saved && (
        <div className="saved-transcript" aria-label="Saved transcript">
          <p className="eyebrow">Saved transcript · {saved.transcript.length} turns</p>
          {saved.transcript.length === 0 ? (
            <p className="muted small">No conversation turns were captured.</p>
          ) : (
            <ol className="transcript">
              {saved.transcript.map((t) => (
                <li key={t.turn ?? t.timestamp} className={`turn turn-${t.speaker}`}>
                  <span className="turn-meta">
                    {t.turn}. {t.speaker === "agent" ? "Agent" : "Customer"} · {formatTime(t.timestamp)}
                  </span>
                  <span>{t.text}</span>
                </li>
              ))}
            </ol>
          )}
          {saved.summary && <p className="muted small">Summary: {saved.summary}</p>}
          <ReviewFlags flags={saved.review_flags ?? []} />
        </div>
      )}
    </section>
  );
}
