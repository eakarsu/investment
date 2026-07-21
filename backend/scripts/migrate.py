"""Apply checksum-verified PostgreSQL migrations; never called by startup."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import asyncpg

from backend.config import settings


def _dsn() -> str:
    return settings.db_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def main() -> None:
    root = Path(__file__).resolve().parents[1] / "migrations"
    connection = await asyncpg.connect(_dsn())
    try:
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
              name TEXT PRIMARY KEY, checksum CHAR(64) NOT NULL,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        for path in sorted(root.glob("*.sql")):
            sql = path.read_text()
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            existing = await connection.fetchval("SELECT checksum FROM schema_migrations WHERE name=$1", path.name)
            if existing:
                if existing != checksum:
                    raise RuntimeError(f"Applied migration checksum changed: {path.name}")
                print(f"already applied {path.name}")
                continue
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute("INSERT INTO schema_migrations(name,checksum) VALUES($1,$2)", path.name, checksum)
            print(f"applied {path.name}")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
