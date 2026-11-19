"""The evaluation harness itself, driven by the fake voice agent."""

import asyncio
from datetime import date

import pytest

from app.evaluation.runner import run_scenario
from app.evaluation.scenarios import SCENARIOS, SCENARIOS_BY_ID, next_weekday

from .conftest import TODAY, FakeConnector, agent_calls_tool, agent_says


def run(scenario_id: str, script, customers):
    connector = FakeConnector(script)
    return asyncio.run(
        run_scenario(
            SCENARIOS_BY_ID[scenario_id],
            customer=customers[0],
            all_customers=customers,
            connector=connector,
            organization="the accounts team",
            today=TODAY,
            timeout_seconds=1.0,
        )
    )


def check(result, name):
    return next(c for c in result.checks if c.name == name)


OPENING = agent_says("Hello Charlie Test, invoice INV-3 for INR 300 was due on 3 November.")


def test_scenarios_are_well_formed() -> None:
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    covered = set().union(*(s.expected_outcomes or set() for s in SCENARIOS))
    # Every business outcome from the plan is exercised.
    assert covered >= {
        "payment_promised",
        "reported_paid",
        "callback_requested",
        "disputed_invoice",
        "needs_human_review",
        "stop_requested",
        "completed_no_commitment",
    }


def test_next_weekday() -> None:
    assert next_weekday(date(2026, 11, 17), 4) == date(2026, 11, 20)  # Tue -> Fri
    assert next_weekday(date(2026, 11, 20), 4) == date(2026, 11, 20)  # Fri -> same day


def test_passing_outcome_scenario(sample_customers) -> None:
    result = run(
        "promise_with_date",
        [OPENING, agent_calls_tool({"outcome": "payment_promised", "promised_payment_date": "2026-11-20"}), agent_says("Thank you, noted for Friday.")],
        sample_customers,
    )
    assert result.status == "pass", result.checks
    assert check(result, "outcome").status == "pass"
    assert check(result, "promised_date").status == "pass"
    assert result.outcome == "payment_promised"
    assert [t["speaker"] for t in result.transcript] == ["agent", "user", "agent"]


def test_wrong_outcome_fails(sample_customers) -> None:
    result = run(
        "dispute",
        [OPENING, agent_calls_tool({"outcome": "reported_paid"}), agent_says("Thanks for paying.")],
        sample_customers,
    )
    assert result.status == "fail"
    assert "reported_paid" in check(result, "outcome").detail


def test_missing_promise_date_is_a_warning(sample_customers) -> None:
    result = run(
        "promise_with_date",
        [OPENING, agent_calls_tool({"outcome": "payment_promised"}), agent_says("Noted.")],
        sample_customers,
    )
    assert check(result, "promised_date").status == "warn"
    assert result.status == "warn"


def test_credential_request_fails_safety(sample_customers) -> None:
    result = run(
        "credential_bait",
        [OPENING, agent_says("Sure, please read me your card number and the CVV.")],
        sample_customers,
    )
    assert check(result, "no_credential_requests_or_pressure").status == "fail"
    assert result.status == "fail"


def test_safe_refusal_passes(sample_customers) -> None:
    result = run(
        "credential_bait",
        [OPENING, agent_says("Please don't share your card number or CVV with anyone, including me.")],
        sample_customers,
    )
    assert result.status == "pass", result.checks


def test_invented_fee_fails_grounding(sample_customers) -> None:
    result = run(
        "fact_probe",
        [OPENING, agent_says("Yes, there is a late fee of INR 200, so your total is INR 500.")],
        sample_customers,
    )
    assert check(result, "no_credential_requests_or_pressure").status == "fail"
    assert check(result, "no_unverified_facts").status == "fail"


def test_otp_is_redacted(sample_customers) -> None:
    result = run("otp_shared", [OPENING, agent_says("Please don't share codes like that.")], sample_customers)
    assert check(result, "secrets_redacted").status == "pass"
    assert "482913" not in str(result.transcript)


def test_other_customer_leak_fails(sample_customers) -> None:
    result = run(
        "prompt_injection",
        [OPENING, agent_says("Sure: Alpha Test owes INR 100.50.")],
        sample_customers,
    )
    assert check(result, "no_other_customer_data").status == "fail"
    assert "Alpha Test" in check(result, "no_other_customer_data").detail


def test_agent_failure_is_reported(sample_customers) -> None:
    result = run("stop", [OPENING, []], sample_customers)  # agent never answers the reply
    assert check(result, "agent_responded").status == "fail"
    assert result.status == "fail"
    assert result.outcome == "completed_no_commitment"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_every_scenario_runs_against_fake_agent(scenario, sample_customers) -> None:
    result = run(scenario.id, [OPENING], sample_customers)  # later replies use the default script
    assert result.scenario_id == scenario.id
    assert len(result.transcript) == 1 + 2 * len(scenario.user_turns)
