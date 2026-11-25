// Mirrors backend/app/schemas. Keep in sync with the FastAPI response models.

export type PaymentStatus = "unpaid" | "partially_paid" | "overdue";

export interface Invoice {
  invoice_id: string;
  invoice_number: string;
  amount_due: number;
  currency: string;
  due_date: string; // YYYY-MM-DD
  payment_status: PaymentStatus;
}

export interface Customer {
  customer_id: string;
  customer_name: string;
  phone_number: string;
  preferred_language: string;
  invoice: Invoice;
}

export interface CustomerListResponse {
  total: number;
  customers: Customer[];
}

export interface QueueState {
  total: number;
  current_index: number;
  position: number | null;
  remaining: number;
  completed: boolean;
  current_customer: Customer | null;
  active_session_id: string | null;
}

export type SessionOutcome =
  | "payment_promised"
  | "reported_paid"
  | "callback_requested"
  | "disputed_invoice"
  | "needs_human_review"
  | "stop_requested"
  | "completed_no_commitment"
  | "technical_failure";

export type Speaker = "agent" | "user";

/** Where transcripts are saved (backend TranscriptStorage.kind). */
export type TranscriptStore = "postgres" | "postgres+azure_blob" | "azure_blob" | "memory";

export interface TranscriptTurn {
  turn?: number;
  speaker: Speaker;
  text: string;
  timestamp: string; // ISO 8601 with timezone
}

export interface SessionRecord {
  session_id: string;
  customer_id: string;
  invoice_id: string;
  sequence_index: number;
  status: "active" | "completed" | "failed";
  started_at: string;
  ended_at: string | null;
  outcome: SessionOutcome | null;
  promised_payment_date: string | null;
  transcript_ref: string | null;
  transcript_store: TranscriptStore | null;
  review_flag_count?: number;
}

/** Saved transcript JSON (schema_version 1.0), read back from storage. */
export interface TranscriptDocument {
  schema_version: "1.0";
  session_id: string;
  customer_id: string;
  invoice_id: string;
  started_at: string;
  ended_at: string;
  session_status: "completed" | "failed";
  outcome: SessionOutcome;
  promised_payment_date: string | null;
  transcript: TranscriptTurn[];
  summary: string | null;
  review_flags?: ReviewFlag[];
}

export interface FinalizeSessionRequest {
  session_status: "completed" | "failed";
  outcome?: SessionOutcome;
  promised_payment_date?: string;
  transcript: TranscriptTurn[];
  summary?: string;
}

export interface FinalizeSessionResponse {
  session: SessionRecord;
  queue: QueueState;
  advanced: boolean;
  already_finalized: boolean;
}

export type ReviewFlagKind =
  | "sensitive_data_request"
  | "unverified_amount"
  | "unverified_invoice"
  | "unverified_date"
  | "pressure_or_policy"
  | "sensitive_data_shared";

/** Heuristic screening result for the operator to review (not proof of a problem). */
export interface ReviewFlag {
  kind: ReviewFlagKind;
  speaker: Speaker;
  detail: string;
  excerpt: string;
}

export interface Health {
  status: "ok";
  agent_configured: boolean;
  transcript_storage: TranscriptStore;
  customer_source?: "postgres" | "json";
}

/** Outcome the model recorded via its tool, already validated by the backend. */
export interface OutcomeProposal {
  outcome: SessionOutcome;
  promised_payment_date: string | null;
  summary: string | null;
  proposed_at: string;
  // The agent is saying goodbye: the call may end by itself.
  end_call?: boolean;
}

export interface ChatResponse {
  agent_turn: TranscriptTurn;
  transcript: TranscriptTurn[];
  proposed_outcome: OutcomeProposal | null;
  review_flags?: ReviewFlag[];
}

/** Browser-side session lifecycle shown in the dashboard. */
export type UiSessionStatus =
  | "idle"
  | "connecting"
  | "active"
  | "ending"
  | "completed"
  | "failed";

// --- Live monitoring (backend/app/schemas/monitoring.py) ---------------------

export interface Page<T> {
  total: number;
  items: T[];
}

export interface CallItem {
  call_id: string;
  client_id: string;
  client_name: string | null;
  status: string;
  duration_seconds: number | null;
  created_at: string;
  updated_at: string;
}

export interface CallTaskItem {
  task_id: string;
  client_id: string;
  client_name: string | null;
  status: string;
  scheduled_at: string;
  created_at: string;
  updated_at: string;
}

export interface CallEventItem {
  event_id: string;
  call_id: string;
  client_name: string | null;
  event_type: string;
  event_data: Record<string, unknown>;
  created_at: string;
}

export interface CallActionItem {
  action_id: string;
  call_id: string;
  client_name: string | null;
  action_type: string;
  action_status: string;
  result: Record<string, unknown>;
  created_at: string;
}

export interface CallTranscriptItem {
  transcript_id: string;
  call_id: string;
  client_name: string | null;
  speaker: string;
  text: string;
  timestamp: string;
}

export interface LatencyStatItem {
  metric: string;
  count: number;
  average_ms: number | null;
  min_ms: number | null;
  max_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  last_ms: number | null;
  last_at: string | null;
}

export interface LatencySummary {
  generated_at: string;
  metrics: LatencyStatItem[];
}

export interface LatencySampleItem {
  seq: number;
  metric: string;
  milliseconds: number;
  at: string;
  label: string | null;
  session_id: string | null;
}

export interface AuditLogItem {
  audit_id: string;
  actor: string;
  action: string;
  timestamp: string;
}

export interface CountBucket {
  key: string;
  count: number;
}

export interface MonitoringSummary {
  generated_at: string;
  total_calls: number;
  calls_today: number;
  active_calls: number;
  completed_calls: number;
  failed_calls: number;
  declined_calls: number;
  average_duration_seconds: number | null;
  total_tasks: number;
  total_events: number;
  total_actions: number;
  total_audit_logs: number;
  calls_by_status: CountBucket[];
  tasks_by_status: CountBucket[];
  outcomes: CountBucket[];
}
