"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { humanize } from "@/lib/format";
import type {
  AuditLogItem,
  CallActionItem,
  CallEventItem,
  CallItem,
  CallTaskItem,
  CallTranscriptItem,
} from "@/lib/types";

import { LiveTable, type Column } from "./LiveTable";

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("en-IN", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}

function seconds(value: number | null): string {
  if (value === null) return "—";
  const m = Math.floor(value / 60);
  return m > 0 ? `${m}m ${value % 60}s` : `${value}s`;
}

function Badge({ value }: { value: string }) {
  let tone = "neutral";
  if (value === "failed" || value === "declined") tone = "bad";
  else if (value === "completed") tone = "ok";
  else if (["initiated", "in_progress", "active"].includes(value)) tone = "live";
  else if (value === "queued") tone = "warn";
  return <span className={`pill pill-${tone}`}>{humanize(value)}</span>;
}

function JsonDialog({ title, value, onClose }: { title: string; value: Record<string, unknown>; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [copied, setCopied] = useState(false);
  const pretty = JSON.stringify(value, null, 2);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(pretty);
      setCopied(true);
    } catch {
      // Clipboard unavailable (e.g. insecure context); the text stays selectable.
    }
  }

  return (
    <dialog
      ref={ref}
      className="json-dialog"
      aria-label={title}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) ref.current?.close(); // backdrop click
      }}
    >
      <div className="json-dialog-head">
        <p className="eyebrow">{title}</p>
        <button type="button" className="btn" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" className="btn" onClick={() => ref.current?.close()}>
          Close
        </button>
      </div>
      <pre className="json-dialog-body">{pretty}</pre>
    </dialog>
  );
}

function Json({ value, title }: { value: Record<string, unknown>; title: string }) {
  const [open, setOpen] = useState(false);
  const text = JSON.stringify(value);
  return (
    <>
      <button type="button" className="json-chip" title="Click to view full details" onClick={() => setOpen(true)}>
        {text}
      </button>
      {open && <JsonDialog title={title} value={value} onClose={() => setOpen(false)} />}
    </>
  );
}

const shortId = (id: string) => id.slice(0, 8);

function Id({ value }: { value: string }) {
  return <code className="id-chip" title={value}>{shortId(value)}</code>;
}

const CALL_COLUMNS: Column<CallItem>[] = [
  { header: "Call ID", cell: (r) => <Id value={r.call_id} /> },
  { header: "Client ID", cell: (r) => <Id value={r.client_id} /> },
  { header: "Customer", cell: (r) => r.client_name ?? "—" },
  { header: "Status", cell: (r) => <Badge value={r.status} /> },
  { header: "Duration", cell: (r) => seconds(r.duration_seconds) },
  { header: "Started", cell: (r) => when(r.created_at) },
  { header: "Updated", cell: (r) => when(r.updated_at) },
];

export function CallsView() {
  return (
    <LiveTable
      label="Calls"
      columns={CALL_COLUMNS}
      rowKey={(r) => r.call_id}
      load={api.listCalls}
      filters={["initiated", "completed", "declined", "failed"]}
    />
  );
}

const TASK_COLUMNS: Column<CallTaskItem>[] = [
  { header: "Task ID", cell: (r) => <Id value={r.task_id} /> },
  { header: "Client ID", cell: (r) => <Id value={r.client_id} /> },
  { header: "Customer", cell: (r) => r.client_name ?? "—" },
  { header: "Status", cell: (r) => <Badge value={r.status} /> },
  { header: "Scheduled", cell: (r) => when(r.scheduled_at) },
  { header: "Updated", cell: (r) => when(r.updated_at) },
];

export function CallTasksView() {
  return (
    <LiveTable
      label="Call tasks"
      columns={TASK_COLUMNS}
      rowKey={(r) => r.task_id}
      load={api.listCallTasks}
      filters={["queued", "in_progress", "completed"]}
    />
  );
}

const EVENT_COLUMNS: Column<CallEventItem>[] = [
  { header: "Time", cell: (r) => when(r.created_at) },
  { header: "Event", cell: (r) => humanize(r.event_type) },
  { header: "Customer", cell: (r) => r.client_name ?? "—" },
  { header: "Call ID", cell: (r) => <Id value={r.call_id} /> },
  { header: "Details", cell: (r) => <Json title="Event details" value={r.event_data} /> },
];

export function CallEventsView() {
  return (
    <LiveTable
      label="Call events"
      columns={EVENT_COLUMNS}
      rowKey={(r) => r.event_id}
      load={api.listCallEvents}
      filters={["session_started", "session_ended", "session_interrupted"]}
      filterLabel="Event"
    />
  );
}

const ACTION_COLUMNS: Column<CallActionItem>[] = [
  { header: "Time", cell: (r) => when(r.created_at) },
  { header: "Action", cell: (r) => humanize(r.action_type) },
  { header: "Status", cell: (r) => <Badge value={r.action_status} /> },
  { header: "Customer", cell: (r) => r.client_name ?? "—" },
  { header: "Call ID", cell: (r) => <Id value={r.call_id} /> },
  { header: "Result", cell: (r) => <Json title="Action result" value={r.result} /> },
];

export function CallActionsView() {
  return (
    <LiveTable
      label="Call actions"
      columns={ACTION_COLUMNS}
      rowKey={(r) => r.action_id}
      load={api.listCallActions}
      filters={["record_outcome"]}
      filterLabel="Action"
    />
  );
}

const TRANSCRIPT_COLUMNS: Column<CallTranscriptItem>[] = [
  { header: "Time", cell: (r) => when(r.timestamp) },
  { header: "Call ID", cell: (r) => <Id value={r.call_id} /> },
  { header: "Customer", cell: (r) => r.client_name ?? "—" },
  {
    header: "Speaker",
    cell: (r) => (
      <span className={`pill pill-${r.speaker === "agent" ? "live" : "neutral"}`}>{humanize(r.speaker)}</span>
    ),
  },
  { header: "Message", cell: (r) => <span className="transcript-text">{r.text}</span> },
];

export function CallTranscriptsView() {
  return (
    <LiveTable
      label="Call transcripts"
      columns={TRANSCRIPT_COLUMNS}
      rowKey={(r) => r.transcript_id}
      load={api.listCallTranscripts}
      filters={["agent", "user"]}
      filterLabel="Speaker"
    />
  );
}

const AUDIT_COLUMNS: Column<AuditLogItem>[] = [
  { header: "Time", cell: (r) => when(r.timestamp) },
  { header: "Actor", cell: (r) => r.actor },
  { header: "Action", cell: (r) => r.action },
];

export function AuditLogsView() {
  return (
    <LiveTable
      label="Audit logs"
      columns={AUDIT_COLUMNS}
      rowKey={(r) => r.audit_id}
      load={(limit, offset) => api.listAuditLogs(limit, offset)}
    />
  );
}
