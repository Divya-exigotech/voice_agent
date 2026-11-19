import { formatDate, formatMoney, humanize } from "@/lib/format";
import type { Customer } from "@/lib/types";

/** Read-only view of the current queue record. There is intentionally no picker. */
export function CurrentCustomerCard({ customer }: { customer: Customer }) {
  const { invoice } = customer;
  return (
    <section className="card" aria-label="Current customer">
      <p className="eyebrow">Current customer</p>
      <h2 className="customer-name">{customer.customer_name}</h2>
      <dl className="facts">
        <div>
          <dt>Invoice</dt>
          <dd>{invoice.invoice_number}</dd>
        </div>
        <div>
          <dt>Amount due</dt>
          <dd className="amount">{formatMoney(invoice.amount_due, invoice.currency)}</dd>
        </div>
        <div>
          <dt>Due date</dt>
          <dd>{formatDate(invoice.due_date)}</dd>
        </div>
        <div>
          <dt>Payment status</dt>
          <dd>
            <span className={`pill pill-${invoice.payment_status}`}>
              {humanize(invoice.payment_status)}
            </span>
          </dd>
        </div>
        <div>
          <dt>Language</dt>
          <dd>{customer.preferred_language}</dd>
        </div>
        <div>
          <dt>Phone (demo only)</dt>
          <dd>{customer.phone_number}</dd>
        </div>
      </dl>
    </section>
  );
}
