from app.schemas.customer import Customer


def build_customer_context(customer: Customer) -> str:
    """Authoritative facts for one session, sent to the agent as a system item.

    Only values from the customer JSON are included so the agent has nothing to invent.
    """
    invoice = customer.invoice
    return (
        "Session facts (authoritative; use only these and never invent others):\n"
        f"- Customer name: {customer.customer_name}\n"
        f"- Preferred language: {customer.preferred_language}\n"
        f"- Invoice number: {invoice.invoice_number}\n"
        f"- Amount due: {invoice.amount_due:f} {invoice.currency}\n"
        f"- Due date: {invoice.due_date.isoformat()}\n"
        f"- Payment status: {invoice.payment_status}\n"
        "This is a browser demo session, not a phone call. Do not state any other amounts "
        "or dates, and never ask for OTP, PIN, CVV, card number, password or bank credentials."
    )


def build_structured_inputs(customer: Customer) -> dict[str, str]:
    """Placeholder values for agents whose instructions/greeting define input variables."""
    invoice = customer.invoice
    return {
        "customer_name": customer.customer_name,
        "invoice_number": invoice.invoice_number,
        "amount_due": f"{invoice.amount_due:f}",
        "currency": invoice.currency,
        "due_date": invoice.due_date.isoformat(),
        "payment_status": invoice.payment_status,
    }
