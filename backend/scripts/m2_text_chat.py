"""M2 harness: chat with the Foundry agent by typing, for one synthetic customer.

Run from the backend folder after `az login` and setting backend/.env:

    python -m scripts.m2_text_chat --customer 1

Type a reply and press Enter. Submit an empty line to end; the outcome is then requested
and validated. Latency and reply checks are printed per turn and summarised at the end.
"""

import argparse
import asyncio

from app.core.config import settings
from app.schemas.customer import Customer
from app.services.agent_context import build_customer_context, build_structured_inputs
from app.services.customer_loader import load_customers
from app.services.foundry_session import (
    AgentConversation,
    AgentSessionError,
    FoundryNotConfiguredError,
    TurnResult,
    foundry_client,
    open_conversation,
)
from app.services.latency import summarize
from app.services.outcome import resolve_outcome
from app.services.reply_checks import find_credential_mentions, find_unsupported_amounts


def _report(label: str, turn: TurnResult, customer: Customer, samples: list[float]) -> None:
    first = f"{turn.first_output_s:.2f}s" if turn.first_output_s is not None else "n/a"
    print(f"Agent: {turn.agent_text}")
    print(f"  [first output {first}, total {turn.total_s:.2f}s] ({label})")
    if turn.first_output_s is not None:
        samples.append(turn.first_output_s)
    creds = find_credential_mentions(turn.agent_text)
    amounts = find_unsupported_amounts(turn.agent_text, customer)
    if creds:
        print(f"  !! mentions sensitive terms: {', '.join(creds)}")
    if amounts:
        print(f"  !! numbers not in customer facts: {', '.join(amounts)}")


async def _chat(conversation: AgentConversation, customer: Customer) -> None:
    samples: list[float] = []
    await conversation.send_context(build_customer_context(customer))
    _report("opening", await conversation.open_turn(), customer, samples)
    while True:
        text = (await asyncio.to_thread(input, "You: ")).strip()
        if not text:
            break
        _report("reply", await conversation.ask(text), customer, samples)

    outcome = await resolve_outcome(conversation)
    print(
        f"\nOutcome: {outcome.outcome.value}  promised_payment_date={outcome.promised_payment_date}"
    )
    summary = summarize(samples)
    if summary:
        print(
            f"First-output latency over {summary.count} turns: "
            f"p50 {summary.p50:.2f}s, p95 {summary.p95:.2f}s, max {summary.max:.2f}s"
        )


async def main(customer_number: int) -> None:
    customers = await load_customers(settings.CUSTOMERS_FILE)
    if not 1 <= customer_number <= len(customers):
        raise SystemExit(f"--customer must be between 1 and {len(customers)}")
    customer = customers[customer_number - 1]
    inputs = build_structured_inputs(customer) if settings.AZURE_AI_USE_STRUCTURED_INPUTS else None

    try:
        async with (
            foundry_client(settings.AZURE_AI_PROJECT_ENDPOINT) as client,
            open_conversation(
                client, settings.AZURE_AI_AGENT_NAME, structured_inputs=inputs
            ) as conversation,
        ):
            print(f"Connected. Chatting as a demo customer ({customer.customer_id}).")
            await _chat(conversation, customer)
    except FoundryNotConfiguredError as exc:
        raise SystemExit(f"Configuration missing: {exc}") from None
    except AgentSessionError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--customer", type=int, default=1, help="1-based customer number")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.customer))
    except KeyboardInterrupt:
        print("\nInterrupted.")
