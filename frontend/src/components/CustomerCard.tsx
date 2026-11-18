import type { Customer } from '@/types/queue'

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 text-lg font-medium text-slate-900">{value}</dd>
    </div>
  )
}

export function CustomerCard({ customer }: { customer: Customer }) {
  const { invoice } = customer
  return (
    <dl className="grid grid-cols-2 gap-6 rounded-xl border border-slate-200 bg-white p-6">
      <Field label="Customer" value={customer.customer_name} />
      <Field label="Invoice" value={invoice.invoice_number} />
      <Field label="Amount due" value={`${invoice.amount_due} ${invoice.currency}`} />
      <Field label="Due date" value={invoice.due_date} />
      <Field label="Payment status" value={invoice.payment_status.replace('_', ' ')} />
    </dl>
  )
}
