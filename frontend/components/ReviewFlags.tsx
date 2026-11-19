import type { ReviewFlag, ReviewFlagKind } from "@/lib/types";

const LABEL: Record<ReviewFlagKind, string> = {
  sensitive_data_request: "Agent may have asked for sensitive data",
  pressure_or_policy: "Agent mentioned fees, penalties or pressure",
  unverified_amount: "Amount not in the customer record",
  unverified_invoice: "Invoice number not in the customer record",
  unverified_date: "Date not in the record or conversation",
  sensitive_data_shared: "Customer shared a code or card number (removed)",
};

/** Heuristic screening results; shown so the operator can review the conversation. */
export function ReviewFlags({ flags }: { flags: ReviewFlag[] }) {
  if (flags.length === 0) return null;
  return (
    <div className="review-flags" role="status" aria-label="Review flags">
      <p className="review-flags-title">
        Review needed · {flags.length} {flags.length === 1 ? "flag" : "flags"}
      </p>
      <ul>
        {flags.map((flag, i) => (
          <li key={i}>
            <strong>{LABEL[flag.kind]}</strong>
            <span className="muted"> — “{flag.excerpt}”</span>
          </li>
        ))}
      </ul>
      <p className="muted small">Automatic screening; it can miss issues or flag harmless wording.</p>
    </div>
  );
}
