export type PaymentStatus = 'unpaid' | 'partially_paid' | 'paid' | 'overdue'

export interface Invoice {
  invoice_id: string
  invoice_number: string
  // Decimal values are serialised as strings by the API.
  amount_due: string
  currency: string
  due_date: string
  payment_status: PaymentStatus
}

export interface Customer {
  customer_id: string
  customer_name: string
  phone_number: string
  preferred_language: string
  invoice: Invoice
}

export interface QueueState {
  total: number
  current_index: number
  current_position: number | null
  remaining: number
  all_completed: boolean
  current_customer: Customer | null
  active_session_id: string | null
}

export interface SessionRecord {
  session_id: string
  customer_id: string
  invoice_id: string
  status: 'active' | 'completed'
  started_at: string
  ended_at: string | null
}

export type SessionUiState =
  | 'idle'
  | 'connecting'
  | 'active'
  | 'ending'
  | 'completed'
  | 'failed'
