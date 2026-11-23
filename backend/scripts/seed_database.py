"""Seed the PostgreSQL database from data/customers.json (idempotent).

Run after `alembic upgrade head`, from the backend directory:

    python -m scripts.seed_database

Creates (or reuses) one organization, then upserts a client and an invoice per
customer and queues one call task for each client that has none queued. Safe to
run repeatedly. DATABASE_URL comes from the environment / .env.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from app.config import get_settings
from app.services.customer_repository import load_customers

logger = logging.getLogger("seed_database")

ORGANIZATION_NAME = "Demo Organization"


async def seed() -> None:
    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")
    customers = await load_customers(settings.customers_json_path)
    # app payment status -> invoices.status (inverse of the configured map).
    invoice_status = {v: k for k, v in settings.database_invoice_status_map.items()}
    queued = settings.database_task_status_queued
    start = datetime.now(UTC)

    async with await psycopg.AsyncConnection.connect(
        settings.database_url.get_secret_value(),
        options=f"-c search_path={settings.database_schema}",
        connect_timeout=10,
    ) as conn:
        cur = await conn.execute(
            "SELECT id FROM organizations WHERE id = %s OR name = %s ORDER BY (id = %s) DESC LIMIT 1",
            (
                settings.database_organization_id,
                ORGANIZATION_NAME,
                settings.database_organization_id,
            ),
        )
        row = await cur.fetchone()
        if row is None:
            cur = await conn.execute(
                """
                INSERT INTO organizations (id, name, timezone, settings)
                VALUES (gen_random_uuid(), %s, %s, %s) RETURNING id
                """,
                (
                    ORGANIZATION_NAME,
                    settings.business_timezone,
                    Jsonb({"currency": settings.database_default_currency}),
                ),
            )
            row = await cur.fetchone()
        org_id = row[0]

        created_tasks = 0
        for index, customer in enumerate(customers):
            invoice = customer.invoice
            cur = await conn.execute(
                """
                INSERT INTO clients (id, organization_id, external_client_id, name, phone, language)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, %s)
                ON CONFLICT (organization_id, external_client_id)
                DO UPDATE SET name = EXCLUDED.name, phone = EXCLUDED.phone,
                              language = EXCLUDED.language, updated_at = now()
                RETURNING id
                """,
                (
                    org_id,
                    customer.customer_id,
                    customer.customer_name,
                    customer.phone_number,
                    customer.preferred_language,
                ),
            )
            client_id = (await cur.fetchone())[0]
            await conn.execute(
                """
                INSERT INTO invoices (id, organization_id, client_id, external_invoice_id,
                                      amount, due_date, status)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s)
                ON CONFLICT (organization_id, external_invoice_id)
                DO UPDATE SET amount = EXCLUDED.amount, due_date = EXCLUDED.due_date,
                              status = EXCLUDED.status, updated_at = now()
                """,
                (
                    org_id,
                    client_id,
                    invoice.invoice_number,
                    invoice.amount_due,
                    invoice.due_date,
                    invoice_status[invoice.payment_status],
                ),
            )
            cur = await conn.execute(
                """
                INSERT INTO call_tasks (id, organization_id, client_id, scheduled_at, status)
                SELECT gen_random_uuid(), %s, %s, %s, %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM call_tasks
                    WHERE organization_id = %s AND client_id = %s AND status = %s
                )
                """,
                (
                    org_id,
                    client_id,
                    start + timedelta(seconds=index),
                    queued,
                    org_id,
                    client_id,
                    queued,
                ),
            )
            created_tasks += cur.rowcount
        await conn.commit()

    logger.info(
        "Seeded organization %s: %d client(s)/invoice(s), %d new call task(s)",
        org_id,
        len(customers),
        created_tasks,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # psycopg's async mode needs the selector loop on Windows.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(seed())


if __name__ == "__main__":
    main()
