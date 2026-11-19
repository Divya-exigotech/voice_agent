import Link from "next/link";

import { humanize } from "@/lib/format";
import type { Customer, SessionRecord } from "@/lib/types";

export interface LastSession {
  session: SessionRecord;
  customer: Customer;
}

/** Short confirmation after End Session; full details live on the Sessions page. */
export function SessionSavedNotice({ last }: { last: LastSession }) {
  const { session, customer } = last;
  return (
    <section className="card saved-notice" aria-label="Session saved">
      <div>
        <p className="eyebrow">Session {session.status === "failed" ? "saved as failed" : "saved"}</p>
        <p>
          <strong>{customer.customer_name}</strong>
          {session.outcome && <> · {humanize(session.outcome)}</>}
        </p>
        <p className="muted small" data-testid="review-flag-count">
          {session.review_flag_count ? (
            <span className="pill pill-overdue">{session.review_flag_count} to review</span>
          ) : (
            "No review flags"
          )}
        </p>
      </div>
      <Link href="/sessions" className="btn">
        View in Sessions
      </Link>
    </section>
  );
}
