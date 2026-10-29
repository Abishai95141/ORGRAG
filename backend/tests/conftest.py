"""Test configuration.

Unit tests need nothing. Integration tests (marked `integration`) run against the
Postgres at TEST_DATABASE_URL (default: the local dev server on :5432, database
`strata_test`) and are skipped when it is unreachable. The schema is rebuilt with
Alembic at the start of the session, so the migration itself is under test."""

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/strata_test"
)
os.environ["DATABASE_URL"] = TEST_DB
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["FILE_STORAGE_DIR"] = tempfile.mkdtemp(prefix="strata-files-")

BACKEND = Path(__file__).resolve().parent.parent


def _db_available() -> bool:
    import asyncpg

    async def probe() -> bool:
        try:
            conn = await asyncpg.connect(TEST_DB.replace("+asyncpg", ""), timeout=3)
        except Exception:
            return False
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await conn.close()
        return True

    return asyncio.run(probe())


_DB_READY: bool | None = None


def pytest_collection_modifyitems(config, items):
    global _DB_READY
    needs_db = [i for i in items if "integration" in i.keywords]
    if not needs_db:
        return
    _DB_READY = _db_available()
    if _DB_READY:
        env = {**os.environ, "ALEMBIC_DATABASE_URL": TEST_DB}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True
        )
        return
    skip = pytest.mark.skip(reason=f"Postgres not reachable at {TEST_DB}")
    for item in needs_db:
        item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs a Postgres with pgvector")


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app

    return fastapi_app
