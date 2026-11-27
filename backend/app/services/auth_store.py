"""Persistent storage for dashboard sign-in: JWT signing key, revoked tokens, rate limits.

Users sign in with Microsoft Entra ID (see app/services/microsoft_sso.py); no
passwords or local accounts are stored.
"""

import asyncio
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

DEFAULT_AUTH_DATABASE = Path.home() / ".voice-agent" / "users.sqlite3"


class AuthStore:
    def __init__(
        self, path: Path = DEFAULT_AUTH_DATABASE, signing_key: bytes | None = None
    ) -> None:
        self.path = path
        self._configured_signing_key = signing_key
        self._signing_key: bytes | None = None

    @property
    def signing_key(self) -> bytes:
        if self._signing_key is None:
            raise RuntimeError("The authentication store has not been initialized.")
        return self._signing_key

    async def initialize(self) -> None:
        if self._configured_signing_key is not None:
            if len(self._configured_signing_key) < 32:
                raise ValueError("The JWT signing key must contain at least 32 bytes.")
            await asyncio.to_thread(self._initialize_schema_sync)
            self._signing_key = self._configured_signing_key
            return
        self._signing_key = await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_sync(self) -> bytes:
        self._initialize_schema_sync()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM auth_metadata WHERE key = 'jwt_signing_key'"
            ).fetchone()
        if row is None:
            raise RuntimeError("The authentication signing key was not initialized.")
        return bytes(row["value"])

    def _initialize_schema_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = int(time.time())
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                    CREATE TABLE IF NOT EXISTS auth_metadata (
                        key TEXT PRIMARY KEY,
                        value BLOB NOT NULL
                    )
                    """
            )
            connection.execute(
                """
                    CREATE TABLE IF NOT EXISTS auth_rate_limits (
                        bucket_key TEXT PRIMARY KEY,
                        attempts INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL
                    )
                    """
            )
            connection.execute(
                """
                    CREATE TABLE IF NOT EXISTS revoked_tokens (
                        token_id TEXT PRIMARY KEY,
                        expires_at INTEGER NOT NULL
                    )
                    """
            )
            connection.execute(
                "DELETE FROM auth_rate_limits WHERE expires_at <= ?", (now,)
            )
            connection.execute(
                "DELETE FROM revoked_tokens WHERE expires_at <= strftime('%s', 'now')"
            )
        if self._configured_signing_key is None:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT value FROM auth_metadata WHERE key = 'jwt_signing_key'"
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO auth_metadata (key, value) VALUES (?, ?)",
                        ("jwt_signing_key", secrets.token_bytes(32)),
                    )
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    async def consume_rate_limit(
        self, scope: str, identifier: str, limit: int, window_seconds: int
    ) -> int:
        return await asyncio.to_thread(
            self._consume_rate_limit_sync,
            scope,
            identifier,
            limit,
            window_seconds,
        )

    def _consume_rate_limit_sync(
        self, scope: str, identifier: str, limit: int, window_seconds: int
    ) -> int:
        now = int(time.time())
        bucket_digest = hmac.new(
            self.signing_key,
            f"{scope}:{identifier}".encode(),
            hashlib.sha256,
        ).hexdigest()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempts, expires_at FROM auth_rate_limits WHERE bucket_key = ?",
                (bucket_digest,),
            ).fetchone()
            if row is None or row["expires_at"] <= now:
                connection.execute(
                    """
                    INSERT INTO auth_rate_limits (bucket_key, attempts, expires_at)
                    VALUES (?, 1, ?)
                    ON CONFLICT(bucket_key) DO UPDATE SET attempts = 1, expires_at = ?
                    """,
                    (bucket_digest, now + window_seconds, now + window_seconds),
                )
                return 0
            if row["attempts"] >= limit:
                return max(1, row["expires_at"] - now)
            connection.execute(
                "UPDATE auth_rate_limits SET attempts = attempts + 1 "
                "WHERE bucket_key = ?",
                (bucket_digest,),
            )
            return 0

    async def revoke_token(self, token_id: str, expires_at: int) -> None:
        await asyncio.to_thread(self._revoke_token_sync, token_id, expires_at)

    def _revoke_token_sync(self, token_id: str, expires_at: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO revoked_tokens (token_id, expires_at) "
                "VALUES (?, ?)",
                (token_id, expires_at),
            )

    async def is_token_revoked(self, token_id: str) -> bool:
        return await asyncio.to_thread(self._is_token_revoked_sync, token_id)

    def _is_token_revoked_sync(self, token_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM revoked_tokens WHERE token_id = ? AND expires_at > ?",
                (token_id, int(time.time())),
            ).fetchone()
        return row is not None
