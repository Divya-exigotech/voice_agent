"""Create and verify the private transcript container in Azure Blob Storage.

Usage (from backend/, venv active, after `az login`):
    python -m scripts.setup_blob              # create container if missing, then verify
    python -m scripts.setup_blob --check-only # verify only, never create

Requires AZURE_STORAGE_ACCOUNT_URL (and optionally AZURE_STORAGE_CONTAINER) in .env.
Your identity needs the "Storage Blob Data Contributor" role on the storage
account (or on the container, once it exists).

Checks:
  1. Microsoft Entra ID sign-in works for the storage account.
  2. The container exists (created private if missing, unless --check-only).
  3. The container does not allow anonymous/public access.
  4. A probe transcript blob can be written, read back and deleted.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from app.config import get_settings
from app.schemas.session import SessionOutcome, TranscriptDocument
from app.services.transcript_storage import BlobTranscriptStorage


def fail(message: str) -> int:
    print(f"\n[FAIL] {message}")
    return 1


async def main(check_only: bool) -> int:
    settings = get_settings()
    print("Configuration")
    print(f"  account url : {settings.azure_storage_account_url or '(not set)'}")
    print(f"  container   : {settings.azure_storage_container}")
    if not settings.azure_storage_account_url:
        return fail(
            "Set AZURE_STORAGE_ACCOUNT_URL in .env (https://<account>.blob.core.windows.net)"
        )

    from azure.core.exceptions import (
        ClientAuthenticationError,
        HttpResponseError,
        ResourceExistsError,
    )
    from azure.storage.blob.aio import BlobServiceClient

    from app.services.azure_auth import build_async_credential

    credential = build_async_credential(settings)
    service = BlobServiceClient(
        settings.azure_storage_account_url, credential=credential
    )
    container = service.get_container_client(settings.azure_storage_container)
    try:
        print("\n[1/4] Signing in and checking the container ...")
        try:
            exists = await container.exists()
        except ClientAuthenticationError as exc:
            return fail(
                f"Sign-in failed. Run `az login` (with the right --tenant).\n{exc}"
            )
        except HttpResponseError as exc:
            return fail(
                f"Storage rejected the request ({exc.status_code}). Check the account URL and "
                "that your identity has 'Storage Blob Data Contributor'.\n"
                f"{exc.message}"
            )

        if not exists:
            if check_only:
                return fail(
                    "The container does not exist (run without --check-only to create it)"
                )
            print("[2/4] Creating private container ...")
            try:
                await container.create_container()  # no public_access => private
            except ResourceExistsError:
                pass
        else:
            print("[2/4] Container exists")

        print("[3/4] Checking that the container is private ...")
        public_access = (await container.get_container_properties()).public_access
        if public_access:
            return fail(
                f"Container allows public access ({public_access}). Disable it: "
                f"az storage container set-permission --name {settings.azure_storage_container} "
                "--public-access off --account-name <account> --auth-mode login"
            )
        print("  OK: no anonymous access")

        print("[4/4] Writing, reading and deleting a probe transcript ...")
        storage = BlobTranscriptStorage(container)
        now = datetime.now(UTC)
        probe = TranscriptDocument(
            session_id="setup_probe",
            customer_id="setup_check",
            invoice_id="setup_check",
            started_at=now,
            ended_at=now,
            session_status="completed",
            outcome=SessionOutcome.COMPLETED_NO_COMMITMENT,
            transcript=[],
            summary="Connectivity probe written by scripts/setup_blob.py",
        )
        name = await storage.save(probe)
        loaded = await storage.load(name)
        await container.delete_blob(name)
        if loaded is None or loaded.session_id != probe.session_id:
            return fail("The probe transcript could not be read back")
        print(f"  OK: {name}")
    finally:
        await service.close()
        await credential.close()

    print(
        f"\n[OK] Transcripts will be saved to {settings.azure_storage_container} (private)."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Verify only; never create the container",
    )
    sys.exit(asyncio.run(main(parser.parse_args().check_only)))
