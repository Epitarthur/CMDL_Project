"""Transactional, checksum-verified forward migrations using libpq PG* settings."""

from hashlib import sha256
from importlib.resources import files

import psycopg


class MigrationError(RuntimeError):
    """The database history differs from the packaged migration history."""


def migrate(connection: psycopg.Connection) -> list[str]:
    """Apply all pending migrations atomically, serializing concurrent migrators.

    Caller supplies an idle connection. Never edit an applied migration: add a new
    numbered SQL file. There is intentionally no destructive downgrade command.
    """
    sources = sorted(files("cmdl.db").joinpath("migrations").iterdir(), key=lambda p: p.name)
    migrations = [(p.name, p.read_bytes()) for p in sources if p.name.endswith(".sql")]
    applied = []
    with connection.transaction():
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        connection.execute("SET LOCAL lock_timeout = '15s'")
        connection.execute("SELECT pg_advisory_xact_lock(68125240716301)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
                applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
            )
        """)
        history = dict(
            connection.execute(
                "SELECT version, sha256 FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        names = [name for name, _ in migrations]
        if list(history) != names[: len(history)]:
            raise MigrationError("Database migration history is unknown or out of order")
        for name, source in migrations:
            digest = sha256(source).hexdigest()
            if name in history:
                if history[name] != digest:
                    raise MigrationError(f"Applied migration checksum differs: {name}")
                continue
            connection.execute(source.decode("utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations(version, sha256) VALUES (%s, %s)", (name, digest)
            )
            applied.append(name)
    return applied
