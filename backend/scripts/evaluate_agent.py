"""Evaluate the Foundry voice agent against the plan's customer-reply scenarios.

Usage (from backend/, venv active, .env filled in, after `az login`):
    python -m scripts.evaluate_agent                      # all scenarios
    python -m scripts.evaluate_agent --list               # show scenario ids
    python -m scripts.evaluate_agent -s reported_paid -s stop
    python -m scripts.evaluate_agent --tag safety --customer-index 2

Each scenario runs a fresh typed session through the app's own conversation
path (session context, outcome tool validation, redaction, response screening)
against the real agent, then checks the saved transcript:
  * expected outcome / promised date,
  * no credential requests or pressure, no unverified amounts/dates/invoices,
  * secrets redacted, no other customers' data.
A JSON report with every transcript is written to backend/reports/.
Exit code 1 if any scenario fails (warnings do not fail the run).

Note: this calls your real agent once per turn and is billed accordingly.
Outcomes come from the agent's record_outcome tool; add it in Foundry first.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.evaluation.runner import ScenarioResult, run_scenario
from app.evaluation.scenarios import SCENARIOS, SCENARIOS_BY_ID
from app.services.azure_auth import build_async_credential
from app.services.customer_repository import load_customers
from app.services.voice_agent import FoundryAgentConnector

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
ICON = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}


def print_result(result: ScenarioResult) -> None:
    print(f"\n[{ICON[result.status]}] {result.scenario_id}: {result.title}")
    print(f"       outcome={result.outcome} promised={result.promised_payment_date}")
    for check in result.checks:
        if check.status != "pass" or check.name in ("outcome", "promised_date"):
            detail = f" - {check.detail}" if check.detail else ""
            print(f"       {ICON[check.status]} {check.name}{detail}")
    for turn in result.transcript:
        who = "Agent" if turn["speaker"] == "agent" else "User "
        print(f"         {who}: {turn['text']}")


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.agent_configured:
        print("[FAIL] Set AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_AGENT_NAME in .env")
        return 1

    scenarios = [SCENARIOS_BY_ID[s] for s in args.scenario] if args.scenario else list(SCENARIOS)
    if args.tag:
        scenarios = [s for s in scenarios if args.tag in s.tags]
    customers = load_customers(settings.customers_json_path)
    customer = customers[args.customer_index]
    today = datetime.now(ZoneInfo(settings.business_timezone)).date()

    print(
        f"Evaluating agent '{settings.azure_ai_agent_name}' with {len(scenarios)} scenario(s) "
        f"as {customer.customer_name} ({customer.invoice.invoice_number}), today={today}"
    )
    connector = FoundryAgentConnector(
        project_endpoint=settings.azure_ai_project_endpoint or "",
        agent_name=settings.azure_ai_agent_name or "",
        credential_factory=lambda: build_async_credential(settings),
    )
    results: list[ScenarioResult] = []
    try:
        for scenario in scenarios:
            result = await run_scenario(
                scenario,
                customer=customer,
                all_customers=customers,
                connector=connector,
                organization=settings.agent_organization_name,
                today=today,
                timeout_seconds=settings.azure_ai_agent_timeout_seconds,
            )
            results.append(result)
            print_result(result)
    finally:
        await connector.aclose()

    counts = {s: sum(r.status == s for r in results) for s in ("pass", "warn", "fail")}
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"evaluation-{datetime.now():%Y%m%d-%H%M%S}.json"
    report_path.write_text(
        json.dumps(
            {
                "agent": settings.azure_ai_agent_name,
                "today": today.isoformat(),
                "customer_id": customer.customer_id,
                "summary": counts,
                "results": [r.to_dict() for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nSummary: {counts['pass']} passed, {counts['warn']} warnings, {counts['fail']} failed")
    print(f"Report: {report_path}")
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-s", "--scenario", action="append", choices=sorted(SCENARIOS_BY_ID), help="Scenario id (repeatable)")
    parser.add_argument("--tag", choices=["outcome", "safety", "privacy", "grounding"], help="Only scenarios with this tag")
    parser.add_argument("--customer-index", type=int, default=0, help="Customer from data/customers.json (default 0)")
    parser.add_argument("--list", action="store_true", help="List scenarios and exit")
    parsed = parser.parse_args()
    if parsed.list:
        for s in SCENARIOS:
            expected = ", ".join(sorted(s.expected_outcomes)) if s.expected_outcomes else "any (safety checks)"
            print(f"{s.id:<22} {s.title}  [expects: {expected}]")
        sys.exit(0)
    sys.exit(asyncio.run(main(parsed)))
