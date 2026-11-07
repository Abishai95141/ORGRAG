"""Build the hard set's database: a pruned copy of a real org plus the generated world.

    .venv/bin/python -m eval.hard.build --source strata

* The source database is only ever read, by pg_dump. Everything else happens in the copy,
  `strata_eval_hard`, and this refuses any other target name.
* The copy keeps one org (523 real documents, their chunks and embeddings) and loses the
  rest: other orgs, people, chat history and connector secrets.
* The world (world.py, render.py) goes in through the normal ingestion path, one upload
  source per collection, with the restricted collections and the people who may read them.
* `manifest.json` records every document's content hash, so check.py can tell if the copy
  has drifted from what the gold answers were proved against.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["EVAL_CORPUS"] = "hard"

# The harness points the app at the eval database, so it must be imported first.
from eval import harness  # noqa: E402, I001

from sqlalchemy import select, text  # noqa: E402

from app.connectors.upload import storage_dir  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.ingestion.pipeline import sync_source  # noqa: E402
from app.models import (  # noqa: E402
    Collection,
    CollectionAccess,
    Membership,
    Organization,
    Source,
    User,
)
from eval.hard import render as r  # noqa: E402
from eval.hard.questions import ASKERS  # noqa: E402
from eval.hard.world import build as build_world  # noqa: E402

ORG = "strata"
MANIFEST = Path(__file__).resolve().parent / "manifest.json"
TOOLS = ("/opt/homebrew/opt/postgresql@17/bin", "/usr/local/bin", "/usr/bin")


def tool(name: str) -> str:
    found = shutil.which(name) or next(
        (f"{d}/{name}" for d in TOOLS if Path(f"{d}/{name}").exists()), None
    )
    if not found:
        sys.exit(f"{name} not found")
    return found


def copy_database(source: str) -> None:
    """pg_dump the source, restore it into a fresh target."""
    base, target = harness.EVAL_DB.replace("+asyncpg", "").rsplit("/", 1)
    if not target.startswith("strata_eval_hard"):
        sys.exit(f"Refusing to build into {target!r}: the hard set's database is strata_eval_hard")
    if source == target:
        sys.exit("The source and the copy can't be the same database")
    admin = f"{base}/postgres"
    psql = tool("psql")
    subprocess.run([psql, admin, "-c", f'DROP DATABASE IF EXISTS "{target}"'], check=True)
    subprocess.run([psql, admin, "-c", f'CREATE DATABASE "{target}"'], check=True)
    with tempfile.TemporaryDirectory() as folder:
        dump = f"{folder}/source.dump"
        subprocess.run([tool("pg_dump"), "-Fc", "-f", dump, f"{base}/{source}"], check=True)
        subprocess.run(
            [tool("pg_restore"), "--no-owner", "-d", f"{base}/{target}", dump], check=True
        )


async def prune() -> None:
    """One org, no people, no secrets, no chat."""
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM organizations WHERE slug <> :s"), {"s": ORG})
        await db.execute(text("DELETE FROM users"))
        await db.execute(text("UPDATE sources SET config = '{}'::jsonb"))
        await db.execute(text("DELETE FROM conversations"))
        await db.commit()


async def add_people() -> None:
    async with SessionLocal() as db:
        org = (await db.execute(select(Organization).where(Organization.slug == ORG))).scalar_one()
        owner = User(
            email=harness.EVAL_EMAIL, name="Eval Runner", password_hash=hash_password("eval")
        )
        db.add(owner)
        await db.flush()
        db.add(Membership(user_id=owner.id, org_id=org.id, role="owner"))
        for key, (name, _) in ASKERS.items():
            user = User(email=f"{key}@strata.dev", name=name, password_hash=hash_password("eval"))
            db.add(user)
            await db.flush()
            db.add(Membership(user_id=user.id, org_id=org.id, role="member"))
        await db.commit()


async def add_world() -> None:
    docs = r.render(build_world())
    async with SessionLocal() as db:
        org = (await db.execute(select(Organization).where(Organization.slug == ORG))).scalar_one()
        # A rebuild replaces the world; the real documents stay as they are.
        await db.execute(text("DELETE FROM collections WHERE name LIKE 'Port Systems %'"))
        await db.commit()
        for name in sorted({d.collection for d in docs}):
            restricted = name in r.RESTRICTED
            col = Collection(
                org_id=org.id, name=name, visibility="restricted" if restricted else "org"
            )
            db.add(col)
            await db.flush()
            source = Source(
                org_id=org.id, collection_id=col.id, kind="upload", name=name, config={}
            )
            db.add(source)
            await db.flush()
            folder = storage_dir(str(source.id))
            for d in docs:
                if d.collection == name:
                    (folder / d.filename).write_text(d.text, encoding="utf-8")
            await db.commit()
            stats = await sync_source(db, source)
            source.status = "ready"
            await db.commit()
            print(f"  {name}: {stats}", flush=True)
            for key, (_, grants) in ASKERS.items():
                if name in grants:
                    user = (
                        await db.execute(select(User).where(User.email == f"{key}@strata.dev"))
                    ).scalar_one()
                    db.add(CollectionAccess(collection_id=col.id, user_id=user.id))
            await db.commit()


async def write_manifest() -> None:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                text("SELECT title, external_id, content_hash FROM documents ORDER BY 2, 1, 3")
            )
        ).all()
    h = hashlib.sha256()
    documents: dict[str, str] = {}
    for title, external_id, content_hash in rows:
        h.update(f"{external_id}\0{content_hash}\n".encode())
        documents[f"{title}|{external_id}"] = content_hash[:16]
    MANIFEST.write_text(
        json.dumps(
            {"digest": h.hexdigest()[:16], "count": len(rows), "documents": documents}, indent=0
        )
    )
    print(f"manifest: {len(rows)} documents, digest {h.hexdigest()[:16]}")


def rechunk() -> None:
    """The source may predate today's chunker; the benchmark measures today's system."""
    env = {**os.environ, "DATABASE_URL": harness.EVAL_DB}
    subprocess.run(
        [sys.executable, "-m", "app.ingestion.rechunk", "--apply"],
        cwd=harness.BACKEND,
        env=env,
        check=True,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=ORG, help="database to copy (read only)")
    p.add_argument("--world-only", action="store_true", help="skip the copy (it's already there)")
    args = p.parse_args()
    if get_settings().database_url.rsplit("/", 1)[1] != "strata_eval_hard":
        sys.exit("The app isn't pointed at strata_eval_hard")
    if not args.world_only:
        copy_database(args.source)
        harness.ensure_database()

    async def fill() -> None:  # one event loop: the app's connection pool belongs to it
        if not args.world_only:
            await prune()
            await add_people()
            await asyncio.to_thread(rechunk)
        await add_world()
        await write_manifest()

    asyncio.run(fill())


if __name__ == "__main__":
    main()
