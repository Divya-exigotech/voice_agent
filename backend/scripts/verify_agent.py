"""Verify the connection to the existing Foundry voice agent, using the app's own code path.

Usage (from backend/, venv active, .env filled in, after `az login`):
    python -m scripts.verify_agent
    python -m scripts.verify_agent --say "I will pay on Friday"
    python -m scripts.verify_agent --print-tool      # JSON for the record_outcome tool

Steps:
  1. Connect to the agent by name (Entra ID via DefaultAzureCredential).
  2. Print what the session reports: model, output modalities, tools.
  3. Add the first JSON customer's context and ask for the opening line.
  4. Optionally send one typed customer reply and print any outcome tool call.
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.services.agent_prompt import (
    RECORD_OUTCOME_TOOL,
    RECORD_OUTCOME_TOOL_NAME,
    build_session_context,
)
from app.services.azure_auth import build_async_credential
from app.services.customer_repository import load_customers
from app.services.outcome_validation import validate_outcome_arguments
from app.services.voice_agent import AgentError, FoundryAgentConnector, LiveAgentSession


async def main(say: str | None) -> int:
    settings = get_settings()
    print("Configuration")
    print(f"  project endpoint : {settings.azure_ai_project_endpoint or '(not set)'}")
    print(f"  agent name       : {settings.azure_ai_agent_name or '(not set)'}")
    if not settings.agent_configured:
        print("\n[FAIL] Set AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_AGENT_NAME in .env")
        return 1

    customer = load_customers(settings.customers_json_path)[0]
    today = datetime.now(ZoneInfo(settings.business_timezone)).date()
    transcript: list[str] = []
    tool_calls: list[str] = []

    def on_turn(speaker: str, text: str, _at: datetime | None = None) -> None:
        transcript.append(f"{speaker}: {text}")
        print(f"  {'Agent' if speaker == 'agent' else 'User '}: {text}")

    def on_tool_call(name: str, arguments: str) -> str:
        result = validate_outcome_arguments(arguments, today=today, now=datetime.now(UTC))
        tool_calls.append(f"{name}({arguments}) -> {'valid' if result.ok else result.error}")
        return json.dumps({"recorded": result.ok, "error": result.error})

    connector = FoundryAgentConnector(
        project_endpoint=settings.azure_ai_project_endpoint or "",
        agent_name=settings.azure_ai_agent_name or "",
        credential_factory=lambda: build_async_credential(settings),
    )
    session = LiveAgentSession(
        connector.connect,
        context=build_session_context(
            customer=customer, today=today, organization=settings.agent_organization_name
        ),
        on_turn=on_turn,
        on_tool_call=on_tool_call,
        timeout_seconds=settings.azure_ai_agent_timeout_seconds,
    )
    try:
        print("\n[1/3] Connecting to the voice agent ...")
        await session.start()
        print(f"  conversation id  : {session.conversation_id}")
        for key, value in session.session_info.items():
            print(f"  {key:<17}: {value}")
        tools = session.session_info.get("tools") or []
        if RECORD_OUTCOME_TOOL_NAME not in tools:
            print(
                f"  [WARN] The agent has no '{RECORD_OUTCOME_TOOL_NAME}' function tool, so "
                "outcomes will not be detected. Add it in Foundry (run with --print-tool)."
            )

        print(f"\n[2/3] Opening line for {customer.customer_name} ...")
        await session.request_reply(None)

        if say:
            print("\n[3/3] Customer reply ...")
            print(f"  User : {say}")
            await session.request_reply(say)
        else:
            print('\n[3/3] Skipped (pass --say "..." to test a customer reply)')
    except AgentError as exc:
        print(
            f"\n[FAIL] {exc}\n\nChecks: `az login` with the right tenant; your account has "
            "access to the Foundry project (e.g. Azure AI User role); the agent name is "
            "correct and the agent is a voice agent."
        )
        return 1
    finally:
        await session.close()
        await connector.aclose()

    for line in tool_calls:
        print(f"  Tool call: {line}")
    print("\n[OK] Connected to the Foundry voice agent and received replies.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--say", help="Optional customer reply to send after the opening line")
    parser.add_argument(
        "--print-tool", action="store_true", help="Print the record_outcome tool JSON and exit"
    )
    args = parser.parse_args()
    if args.print_tool:
        print(json.dumps(RECORD_OUTCOME_TOOL, indent=2))
        sys.exit(0)
    sys.exit(asyncio.run(main(args.say)))
