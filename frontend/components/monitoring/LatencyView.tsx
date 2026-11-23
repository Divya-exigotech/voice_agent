"use client";

import { api } from "@/lib/api";
import { humanize } from "@/lib/format";
import { useLive } from "@/lib/useLive";
import type { LatencySampleItem, LatencyStatItem } from "@/lib/types";

import { LiveTable, type Column } from "./LiveTable";

const TITLES: Record<string, { title: string; hint: string }> = {
  voice_reply: { title: "Voice reply", hint: "customer stops speaking → first agent audio" },
  db_session_start: { title: "Session start", hint: "recording a call start in the database" },
  db_live_write: { title: "Live transcript write", hint: "saving transcript turns during a call" },
  db_final_save: { title: "Final save", hint: "saving a finished call" },
  http: { title: "API requests", hint: "backend response time (dashboard polling excluded)" },
};

function ms(value: number | null): string {
  if (value === null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

function MetricCard({ stat }: { stat: LatencyStatItem }) {
  const info = TITLES[stat.metric] ?? { title: humanize(stat.metric), hint: "" };
  return (
    <section className="card latency-card" aria-label={info.title}>
      <div className="latency-card-head">
        <p className="eyebrow">{info.title}</p>
      </div>
      <p className="latency-big">{ms(stat.average_ms)}</p>
      <p className="muted small">average · {info.hint}</p>
    </section>
  );
}

const SAMPLE_COLUMNS: Column<LatencySampleItem>[] = [
  { header: "Time", cell: (r) => new Date(r.at).toLocaleTimeString("en-IN") },
  { header: "Metric", cell: (r) => (TITLES[r.metric]?.title ?? humanize(r.metric)) },
  { header: "Latency", cell: (r) => <strong>{ms(r.milliseconds)}</strong> },
  {
    header: "Call ID",
    cell: (r) => (r.session_id ? <code className="id-chip" title={r.session_id}>{r.session_id.slice(0, 8)}</code> : "—"),
  },
  { header: "Request", cell: (r) => r.label ?? "—" },
];

export function LatencyView() {
  const { data, error } = useLive(() => api.getLatencySummary(), []);
  return (
    <div className="dashboard">
      {error && !data && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data && (
        <div className="latency-cards">
          {data.metrics.map((m) => (
            <MetricCard key={m.metric} stat={m} />
          ))}
        </div>
      )}
      <p className="muted small">
        Figures cover the time since the backend last started and update as calls start and end.
      </p>
      <LiveTable
        label="Recent measurements"
        columns={SAMPLE_COLUMNS}
        rowKey={(r) => String(r.seq)}
        load={api.listLatencySamples}
        filters={["voice_reply", "db_session_start", "db_live_write", "db_final_save", "http"]}
        filterLabel="Metric"
      />
    </div>
  );
}
