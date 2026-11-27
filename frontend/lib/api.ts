import type {
  AuditLogItem,
  CallActionItem,
  CallEventItem,
  CallItem,
  CallTaskItem,
  CallTranscriptItem,
  ChatResponse,
  CustomerListResponse,
  FinalizeSessionRequest,
  FinalizeSessionResponse,
  Health,
  LatencySampleItem,
  LatencySummary,
  MonitoringSummary,
  Page,
  QueueState,
  SessionRecord,
  TranscriptDocument,
} from "./types";

// Requests go to same-origin /api/*, which next.config.ts rewrites to the
// FastAPI backend. No backend URL or credentials end up in the browser bundle.
const BASE = "/api";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the backend. Is the FastAPI server running?");
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail) detail = JSON.stringify(body.detail);
    } catch {
      // Non-JSON error body (e.g. proxy error when the backend is down).
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

function pageQuery(limit: number, offset: number, filter?: string, filterKey = "status"): string {
  const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (filter) q.set(filterKey, filter);
  return `?${q.toString()}`;
}

export const api = {
  getHealth: () => request<Health>("/health"),

  getQueue: () => request<QueueState>("/queue"),

  listCustomers: () => request<CustomerListResponse>("/customers"),

  listSessions: () => request<SessionRecord[]>("/sessions"),

  startSession: (expectedCustomerId: string) =>
    request<SessionRecord>("/sessions", {
      method: "POST",
      body: JSON.stringify({ expected_customer_id: expectedCustomerId }),
    }),

  finalizeSession: (sessionId: string, payload: FinalizeSessionRequest) =>
    request<FinalizeSessionResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/transcript`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  /** Typed turn with the agent; `text: null` requests the agent's opening line. */
  sendMessage: (sessionId: string, text: string | null) =>
    request<ChatResponse>(`/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  /** Saved transcript, read back from storage (Azure Blob) through the backend. */
  getTranscript: (sessionId: string) =>
    request<TranscriptDocument>(`/sessions/${encodeURIComponent(sessionId)}/transcript`),

  // Live monitoring (read-only; needs the PostgreSQL database).
  getMonitoringSummary: () => request<MonitoringSummary>("/monitoring/summary"),
  listCalls: (limit: number, offset: number, filter?: string) =>
    request<Page<CallItem>>(`/monitoring/calls${pageQuery(limit, offset, filter)}`),
  listCallTasks: (limit: number, offset: number, filter?: string) =>
    request<Page<CallTaskItem>>(`/monitoring/call-tasks${pageQuery(limit, offset, filter)}`),
  listCallEvents: (limit: number, offset: number, filter?: string) =>
    request<Page<CallEventItem>>(
      `/monitoring/call-events${pageQuery(limit, offset, filter, "event_type")}`,
    ),
  listCallActions: (limit: number, offset: number, filter?: string) =>
    request<Page<CallActionItem>>(
      `/monitoring/call-actions${pageQuery(limit, offset, filter, "action_type")}`,
    ),
  listCallTranscripts: (limit: number, offset: number, filter?: string) =>
    request<Page<CallTranscriptItem>>(
      `/monitoring/call-transcripts${pageQuery(limit, offset, filter, "speaker")}`,
    ),
  getLatencySummary: () => request<LatencySummary>("/monitoring/latency/summary"),
  listLatencySamples: (limit: number, offset: number, filter?: string) =>
    request<Page<LatencySampleItem>>(
      `/monitoring/latency/samples${pageQuery(limit, offset, filter, "metric")}`,
    ),
  listAuditLogs: (limit: number, offset: number) =>
    request<Page<AuditLogItem>>(`/monitoring/audit-logs${pageQuery(limit, offset)}`),

  resetQueue: () => request<QueueState>("/queue/reset", { method: "POST" }),
};
