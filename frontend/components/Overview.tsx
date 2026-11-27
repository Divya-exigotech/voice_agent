"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { formatDuration, formatMoney, humanize } from "@/lib/format";
import type { Customer, Health, QueueState, SessionRecord } from "@/lib/types";

import { AreaChart, BarChart, Donut, Sparkline, type Point } from "./charts";
import { SystemStatus } from "./SystemStatus";

const ICONS: Record<string, string> = {
  primary: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  success: "M22 11.1V12a10 10 0 1 1-5.9-9.1M22 4 12 14l-3-3",
  warning: "M12 6v6l4 2M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z",
  danger: "M12 8v4M12 16h.01M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z",
};

function StatCard({
  label,
  value,
  hint,
  tone,
  spark,
}: {
  label: string;
  value: string | number;
  hint: string;
  tone: "primary" | "success" | "warning" | "danger";
  spark?: number[];
}) {
  return (
    <section className={`stat stat-${tone}`} aria-label={label}>
      <span className="stat-icon" aria-hidden="true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d={ICONS[tone]} />
        </svg>
      </span>
      <p className="stat-label">{label}</p>
      <div className="stat-row">
        <p className="stat-value">{value}</p>
        {spark && <Sparkline values={spark} />}
      </div>
      <p className="stat-hint">{hint}</p>
    </section>
  );
}

function count<T>(items: T[], key: (item: T) => string): [string, number][] {
  const map = new Map<string, number>();
  for (const item of items) map.set(key(item), (map.get(key(item)) ?? 0) + 1);
  return [...map.entries()].sort((a, b) => b[1] - a[1]);
}

export function Overview() {
  const [queue, setQueue] = useState<QueueState | null>(null);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getQueue(), api.listSessions(), api.listCustomers()]).then(
      ([q, s, c]) => {
        if (cancelled) return;
        setQueue(q);
        setSessions(s);
        setCustomers(c.customers);
      },
      (err) => !cancelled && setError(err instanceof Error ? err.message : String(err)),
    );
    api.getHealth().then(
      (h) => !cancelled && setHealth(h),
      (err) => !cancelled && setHealthError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="card error-card" role="alert">
        <p>Could not load the overview: {error}</p>
      </div>
    );
  }
  if (!queue) return <p className="muted">Loading overview…</p>;

  const finished = sessions
    .filter((s) => s.status !== "active")
    .sort((x, y) => x.started_at.localeCompare(y.started_at));
  const failed = sessions.filter((s) => s.status === "failed").length;
  const flagged = sessions.filter((s) => (s.review_flag_count ?? 0) > 0).length;
  const outcomes = count(
    finished.filter((s) => s.outcome),
    (s) => s.outcome as string,
  );
  const names = new Map(customers.map((c) => [c.customer_id, c.customer_name]));
  const secs = (s: SessionRecord) =>
    s.ended_at ? Math.max(0, (new Date(s.ended_at).getTime() - new Date(s.started_at).getTime()) / 1000) : 0;
  const durations = finished.map(secs);
  const avg = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0;
  const resolved = finished.filter((s) => s.outcome === "payment_promised" || s.outcome === "reported_paid").length;
  const resolutionRate = finished.length ? Math.round((resolved / finished.length) * 100) : 0;

  // Calls per day, last 7 days.
  const days: Point[] = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - (6 - i));
    const next = new Date(d);
    next.setDate(d.getDate() + 1);
    const n = finished.filter((s) => {
      const t = new Date(s.started_at).getTime();
      return t >= d.getTime() && t < next.getTime();
    }).length;
    return { label: d.toLocaleDateString("en-IN", { weekday: "short" }), value: n, display: `${n} calls` };
  });
  // Handle time per call, most recent 10.
  const trend: Point[] = finished.slice(-10).map((s, i, arr) => ({
    label: `#${finished.length - arr.length + i + 1}`,
    value: Math.round(secs(s)),
    display: formatDuration(secs(s) * 1000),
  }));
  // Payments, from the invoices in the queue plus what customers said on the calls.
  const currency = customers[0]?.invoice.currency ?? "INR";
  const money = (n: number) => formatMoney(n, currency);
  const lastOutcome = new Map<string, string | null>();
  for (const s of finished) lastOutcome.set(s.customer_id, s.outcome);
  const sum = (list: Customer[]) => list.reduce((t, c) => t + c.invoice.amount_due, 0);
  const paidList = customers.filter((c) => lastOutcome.get(c.customer_id) === "reported_paid");
  const promisedList = customers.filter((c) => lastOutcome.get(c.customer_id) === "payment_promised");
  const openList = customers.filter((c) => !paidList.includes(c));
  const overdueList = openList.filter((c) => c.invoice.payment_status === "overdue");
  const statusRows = (["unpaid", "partially_paid", "overdue"] as const).map((st) => {
    const list = customers.filter((c) => c.invoice.payment_status === st);
    return { st, n: list.length, amount: sum(list) };
  });
  const recent = [...finished].reverse().slice(0, 6);

  return (
    <div className="dashboard">
      <div className="overview-head">
        <p className="overview-kicker">Workspace status</p>
        <h2>Follow-up overview</h2>
        <p className="muted small">Current queue and call performance at a glance.</p>
      </div>

      <div className="stats">
        <StatCard label="Calls" value={finished.length} hint={`${queue.total} customers in the queue`} tone="primary" />
        <StatCard
          label="Resolution rate"
          value={`${resolutionRate}%`}
          hint={`${resolved} payment promised or reported paid`}
          tone="success"
        />
        <StatCard
          label="Avg. handle time"
          value={durations.length ? formatDuration(avg * 1000) : "—"}
          hint="Per finished call"
          tone="warning"
          spark={durations.slice(-10)}
        />
        <StatCard
          label="Needs attention"
          value={failed + flagged}
          hint={`${failed} failed · ${flagged} with review flags`}
          tone="danger"
        />
      </div>

      <div className="overview-head">
        <p className="overview-kicker">Payments</p>
        <h2>Invoice and payment status</h2>
      </div>
      <div className="stats">
        <StatCard label="Total outstanding" value={money(sum(openList))} hint={`${openList.length} invoices still open`} tone="primary" />
        <StatCard label="Reported paid" value={money(sum(paidList))} hint={`${paidList.length} customers said they paid`} tone="success" />
        <StatCard label="Promised to pay" value={money(sum(promisedList))} hint={`${promisedList.length} payment promises`} tone="warning" />
        <StatCard label="Overdue" value={money(sum(overdueList))} hint={`${overdueList.length} overdue invoices`} tone="danger" />
      </div>
      <section className="card" aria-label="Invoice status breakdown">
        <p className="eyebrow">Invoices by status</p>
        <table className="table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Customers</th>
              <th>Amount due</th>
            </tr>
          </thead>
          <tbody>
            {statusRows.map((r) => (
              <tr key={r.st}>
                <td>
                  <span className={`pill pill-${r.st}`}>{humanize(r.st)}</span>
                </td>
                <td>{r.n}</td>
                <td>{money(r.amount)}</td>
              </tr>
            ))}
            <tr>
              <td>
                <strong>Total</strong>
              </td>
              <td>
                <strong>{customers.length}</strong>
              </td>
              <td>
                <strong>{money(sum(customers))}</strong>
              </td>
            </tr>
          </tbody>
        </table>
        <p className="muted small" style={{ marginTop: 10 }}>
          &ldquo;Paid&rdquo; counts customers who reported paying during a call; it is not verified against a payment system.
        </p>
      </section>

      <div className="chart-row">
        <section className="card" aria-label="Call volume">
          <p className="eyebrow">Call volume · last 7 days</p>
          <BarChart data={days} />
        </section>
        <section className="card" aria-label="Handle time trend">
          <p className="eyebrow">Handle time per call (seconds)</p>
          {trend.length === 0 ? (
            <p className="muted small chart-empty">No finished calls yet.</p>
          ) : (
            <AreaChart data={trend} />
          )}
        </section>
      </div>

      <div className="chart-row">
        <section className="card" aria-label="Recent calls">
          <p className="eyebrow">Recent calls</p>
          {recent.length === 0 ? (
            <p className="muted small">No finished sessions yet.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Time</th>
                  <th>Duration</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((s) => (
                  <tr key={s.session_id}>
                    <td>{names.get(s.customer_id) ?? s.customer_id}</td>
                    <td>{new Date(s.started_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}</td>
                    <td>{formatDuration(secs(s) * 1000)}</td>
                    <td>{s.outcome ? humanize(s.outcome) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <section className="card" aria-label="Queue status">
          <p className="eyebrow">Queue status</p>
          <div className="queue-status">
            <Donut done={queue.current_index} total={queue.total} centerLabel="processed" />
            <ul className="legend">
              <li>
                <span className="swatch swatch-done" />
                <strong>{queue.current_index}</strong> Completed
              </li>
              <li>
                <span className="swatch swatch-left" />
                <strong>{queue.remaining}</strong> Not completed
              </li>
            </ul>
          </div>
          {outcomes.length > 0 && (
            <ul className="breakdown">
              {outcomes.map(([name, n]) => (
                <li key={name}>
                  <span>{humanize(name)}</span>
                  <strong>{n}</strong>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <SystemStatus health={health} error={healthError} />
    </div>
  );
}
