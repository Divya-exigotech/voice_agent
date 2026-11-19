import { formatDate, formatMoney, humanize } from "@/lib/format";
import type { Customer } from "@/lib/types";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");
}

/** Read-only view of the current queue record. There is intentionally no picker. */
export function CurrentCustomerCard({ customer }: { customer: Customer }) {
  const { invoice } = customer;
  return (
    <section className="card customer-card" aria-label="Current customer">
      <p className="eyebrow">Current customer</p>
      <div className="customer-head">
        <span className="avatar" aria-hidden="true">
          {initials(customer.customer_name)}
        </span>
        <div>
          <h2 className="customer-name">{customer.customer_name}</h2>
          <p className="muted small">
            {customer.preferred_language} · {customer.phone_number}
          </p>
        </div>
      </div>

      <div className="amount-panel">
        <div>
          <p className="amount-label">Amount due</p>
          <p className="amount-value">{formatMoney(invoice.amount_due, invoice.currency)}</p>
        </div>
        <span className={`pill pill-${invoice.payment_status}`}>{humanize(invoice.payment_status)}</span>
      </div>

      <dl className="facts">
        <div>
          <dt>Invoice</dt>
          <dd>{invoice.invoice_number}</dd>
        </div>
        <div>
          <dt>Due date</dt>
          <dd>{formatDate(invoice.due_date)}</dd>
        </div>
      </dl>
    </section>
  );
}
