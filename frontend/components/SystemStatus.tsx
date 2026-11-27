import type { Health, TranscriptStore } from "@/lib/types";

const STORE_LABELS: Record<TranscriptStore, string> = {
  postgres: "database",
  "postgres+azure_blob": "database + Azure Blob (private)",
  azure_blob: "Azure Blob (private)",
  memory: "in memory only (not persisted)",
};

/** Demo readiness at a glance: is the agent configured, where do transcripts go. */
export function SystemStatus({ health, error }: { health: Health | null; error: string | null }) {
  if (error) {
    return (
      <div className="system-status" aria-label="System status">
        <span className="chip chip-bad">Backend unreachable</span>
      </div>
    );
  }
  if (!health) return null;
  return (
    <div className="system-status" aria-label="System status">
      <span className={`chip ${health.agent_configured ? "chip-ok" : "chip-bad"}`}>
        Voice agent: {health.agent_configured ? "configured" : "not configured"}
      </span>
      <span className={`chip ${health.transcript_storage === "memory" ? "chip-warn" : "chip-ok"}`}>
        Transcripts: {STORE_LABELS[health.transcript_storage] ?? health.transcript_storage}
      </span>
      {health.customer_source === "postgres" && <span className="chip chip-ok">Queue: database</span>}
    </div>
  );
}
