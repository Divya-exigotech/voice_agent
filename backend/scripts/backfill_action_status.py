"""Backfill call_actions.action_status for calls that finished before it was set.

Run from the backend directory:

    python -m scripts.backfill_action_status

Only touches this app's outcome actions that still hold the column default
("pending"), deriving the status from the recorded outcome. Idempotent: rows
already updated, or with an unknown outcome, are left alone.
"""

import asyncio
import logging

import psycopg

from app.config import get_settings
from app.services.database import _ACTION_STATUS_BY_OUTCOME, OUTCOME_ACTION, SOURCE

logger = logging.getLogger("backfill_action_status")


async def backfill() -> None:
    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")

    async with await psycopg.AsyncConnection.connect(
        settings.database_url.get_secret_value(),
        options=f"-c search_path={settings.database_schema}",
        connect_timeout=10,
    ) as conn:
        total = 0
        for outcome, status in _ACTION_STATUS_BY_OUTCOME.items():
            cur = await conn.execute(
                """
                UPDATE call_actions SET action_status = %s
                WHERE action_type = %s
                  AND action_status = 'pending'
                  AND "result"->>'source' = %s
                  AND "result"->>'outcome' = %s
                """,
                (status, OUTCOME_ACTION, SOURCE, outcome.value),
            )
            logger.info("%s -> %s: %d row(s)", outcome.value, status, cur.rowcount)
            total += cur.rowcount
        logger.info("Updated %d call action(s)", total)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # psycopg's async mode needs the selector loop on Windows.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(backfill())


if __name__ == "__main__":
    main()
