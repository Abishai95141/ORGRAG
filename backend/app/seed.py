"""Demo data: `python -m app.seed`.

Creates two organizations with collections, upload sources and sample documents
(indexed synchronously), one agent per org, a restricted collection, a second
user to demonstrate permissions, and a little search history for Insights.
Re-running resets the demo orgs."""

import asyncio
import random
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.connectors.upload import storage_dir
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.ingestion.pipeline import sync_source
from app.models import (
    Agent,
    Collection,
    CollectionAccess,
    Membership,
    Organization,
    SearchEvent,
    Source,
    User,
)
from app.schemas.common import OrgSettings

SEED_DIR = Path(__file__).parent / "seed_data"
DEMO_PASSWORD = "strata-demo"

USERS = [
    {"email": "demo@strata.dev", "name": "Jordan Ellis", "avatar_hue": 24},
    {"email": "sam@strata.dev", "name": "Sam Rivera", "avatar_hue": 200},
]

ORGS = [
    {
        "slug": "northwind",
        "name": "Northwind Labs",
        "hue": 24,
        "description": "Real-time data pipelines for product and growth teams.",
        "roles": {"demo@strata.dev": "owner", "sam@strata.dev": "member"},
        "collections": [
            (
                "engineering",
                "Engineering",
                "Handbook, runbooks, specs and postmortems.",
                "code",
                "org",
            ),
            ("company", "Company", "Policies, benefits, pricing and onboarding.", "book", "org"),
            (
                "leadership",
                "Leadership",
                "Board materials and plans. Restricted.",
                "shield",
                "restricted",
            ),
        ],
        "agent": {
            "name": "Eng Navigator",
            "description": "Answers engineering process, on-call and incident questions.",
            "instructions": "Prefer the engineering handbook and runbooks. When an answer "
            "involves an incident, mention its ID (for example INC-2291).",
            "mode": "quick",
            "glyph": "compass",
            "collections": ["engineering"],
            "starter_prompts": [
                "What's our deploy policy on Fridays?",
                "What caused the August ingest outage?",
                "How do I declare a SEV-1?",
            ],
        },
        "questions": [
            "What's our Friday deploy policy?",
            "How much discount can an AE approve?",
            "What caused INC-2291?",
            "How long is the on-call shift?",
            "Is business class allowed for long flights?",
            "What is the learning budget?",
            "How do streaming joins count against quota?",
        ],
    },
    {
        "slug": "halcyon",
        "name": "Halcyon Health",
        "hue": 168,
        "description": "Primary care clinics and telehealth across six states.",
        "roles": {"demo@strata.dev": "owner", "sam@strata.dev": "viewer"},
        "collections": [
            (
                "clinical-operations",
                "Clinical Operations",
                "Intake, escalation, telehealth and incident reviews.",
                "leaf",
                "org",
            ),
            (
                "people-and-policy",
                "People & Policy",
                "HIPAA, onboarding, billing and scheduling policies.",
                "people",
                "org",
            ),
        ],
        "agent": {
            "name": "Care Ops Assistant",
            "description": "Researches clinical operations and policy questions in depth.",
            "instructions": "This is a healthcare organization. Be precise about thresholds "
            "and timeframes, and always surface the escalation path when patient safety "
            "is involved.",
            "mode": "deep",
            "glyph": "shield",
            "collections": [],
            "starter_prompts": [
                "When must a critical lab result reach the clinician?",
                "Walk me through new patient check-in.",
                "What did we change after the duplicate reminder incident?",
            ],
        },
        "questions": [
            "What are the self-pay rates?",
            "When do I escalate a fever?",
            "How much PTO rolls over?",
            "What's the door-to-room target?",
            "Who do I report a misdirected fax to?",
        ],
    },
]


async def _user(db, spec: dict) -> User:
    user = (
        await db.execute(select(User).where(func.lower(User.email) == spec["email"]))
    ).scalar_one_or_none()
    if user is None:
        user = User(**spec, password_hash=hash_password(DEMO_PASSWORD))
        db.add(user)
        await db.flush()
    return user


async def _reset_org(db, slug: str) -> None:
    org = (
        await db.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if org is None:
        return
    source_ids = (await db.execute(select(Source.id).where(Source.org_id == org.id))).scalars()
    for sid in source_ids:
        shutil.rmtree(Path(get_settings().file_storage_dir) / str(sid), ignore_errors=True)
    await db.delete(org)
    await db.flush()


async def seed() -> None:
    async with SessionLocal() as db:
        users = {spec["email"]: await _user(db, spec) for spec in USERS}
        await db.commit()

        for spec in ORGS:
            await _reset_org(db, spec["slug"])
            org = Organization(
                slug=spec["slug"],
                name=spec["name"],
                hue=spec["hue"],
                description=spec["description"],
                settings=OrgSettings(default_mode=spec["agent"]["mode"]).model_dump(),
            )
            db.add(org)
            await db.flush()
            for email, role in spec["roles"].items():
                db.add(Membership(user_id=users[email].id, org_id=org.id, role=role))

            collections: dict[str, Collection] = {}
            for key, name, desc, glyph, visibility in spec["collections"]:
                col = Collection(
                    org_id=org.id, name=name, description=desc, glyph=glyph, visibility=visibility
                )
                db.add(col)
                await db.flush()
                if visibility == "restricted":
                    db.add(
                        CollectionAccess(collection_id=col.id, user_id=users["demo@strata.dev"].id)
                    )
                collections[key] = col

                source = Source(
                    org_id=org.id,
                    collection_id=col.id,
                    kind="upload",
                    name=f"{name} documents",
                    config={},
                    status="syncing",
                )
                db.add(source)
                await db.flush()
                folder = storage_dir(str(source.id))
                for path in sorted((SEED_DIR / spec["slug"] / key).glob("*.md")):
                    shutil.copy(path, folder / path.name)
                await db.commit()
                stats = await sync_source(db, source)
                source.status = "ready"
                source.last_synced_at = datetime.now(UTC)
                source.progress = None
                await db.commit()
                print(f"  {spec['name']} / {name}: indexed {stats['created']} documents")

            a = spec["agent"]
            db.add(
                Agent(
                    org_id=org.id,
                    name=a["name"],
                    description=a["description"],
                    instructions=a["instructions"],
                    mode=a["mode"],
                    glyph=a["glyph"],
                    collection_ids=[collections[k].id for k in a["collections"]],
                    starter_prompts=a["starter_prompts"],
                    created_by=users["demo@strata.dev"].id,
                )
            )

            # A little history so Insights has something to show.
            rng = random.Random(spec["slug"])
            now = datetime.now(UTC)
            for day in range(14):
                for _ in range(rng.randint(0, 6)):
                    db.add(
                        SearchEvent(
                            org_id=org.id,
                            user_id=rng.choice(list(users.values())).id,
                            kind="chat",
                            query=rng.choice(spec["questions"]),
                            latency_ms=rng.randint(40, 220),
                            hits=rng.randint(2, 8),
                            created_at=now - timedelta(days=day, minutes=rng.randint(0, 600)),
                        )
                    )
            await db.commit()

    print("\nSeed complete. Sign in with:")
    print(f"  demo@strata.dev / {DEMO_PASSWORD}   (owner of Northwind Labs and Halcyon Health)")
    print(f"  sam@strata.dev  / {DEMO_PASSWORD}   (member/viewer — can't see Leadership)")


if __name__ == "__main__":
    asyncio.run(seed())
