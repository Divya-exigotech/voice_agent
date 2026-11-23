"use client";

import { api } from "@/lib/api";
import { humanize } from "@/lib/format";
import { useLive } from "@/lib/useLive";
import type { CountBucket } from "@/lib/types";

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <section className="stat stat-primary" aria-label={label}>
      <p className="stat-label">{label}</p>
      <p className="stat-value">{value}</p>
      {hint && <p className="stat-hint">{hint}</p>}
    </section>
  );
}

function Breakdown({ title, rows }: { title: string; rows: CountBucket[] }) {
  return (
    <section className="card" aria-label={title}>
      <p className="eyebrow">{title}</p>
      {rows.length === 0 ? (
        <p className="muted small">Nothing recorded yet.</p>
      ) : (
        <table className="table">
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td>{humanize(r.key)}</td>
                <td style={{ textAlign: "right" }}>{r.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Live call totals straight from the database; refreshes while calls run. */
export function LiveSummary() {
  const { data, error, updatedAt } = useLive(() => api.getMonitoringSummary(), []);

  if (error && !data) {
    return (
      <section className="card" aria-label="Live call activity">
        <p className="eyebrow">Live call activity</p>
        <p className="muted small">Live database figures are unavailable: {error}</p>
      </section>
    );
  }
  if (!data) return null;

  const avg = data.average_duration_seconds;
  return (
    <div className="dashboard">
      <div className="overview-head">
        <p className="overview-kicker">Live from the database</p>
        <h2 className="overview-title">Call activity</h2>
        <p className="muted small">
          <span className={`live-dot${error ? " live-dot-off" : ""}`} aria-hidden="true" />
          {error ? "Disconnected" : "Live"}
          {updatedAt && !error && ` · updated ${updatedAt.toLocaleTimeString("en-IN")}`}
        </p>
      </div>
      <div className="stats">
        <Tile label="Total calls" value={data.total_calls} hint={`${data.calls_today} today`} />
        <Tile label="Active now" value={data.active_calls} hint="calls in progress" />
        <Tile
          label="Completed"
          value={data.completed_calls}
          hint={`${data.failed_calls} failed · ${data.declined_calls} declined`}
        />
        <Tile label="Avg duration" value={avg === null ? "—" : `${Math.round(avg)}s`} hint="per call" />
        <Tile label="Call tasks" value={data.total_tasks} />
        <Tile label="Call events" value={data.total_events} />
        <Tile label="Call actions" value={data.total_actions} />
        <Tile label="Audit logs" value={data.total_audit_logs} />
      </div>
      <div className="grid">
        <Breakdown title="Calls by status" rows={data.calls_by_status} />
        <Breakdown title="Tasks by status" rows={data.tasks_by_status} />
        <Breakdown title="Outcomes" rows={data.outcomes} />
      </div>
    </div>
  );
}
