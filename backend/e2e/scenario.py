"""End-to-end scenario against the running API and worker (real Postgres, real models);
see e2e/README.md.

Northwind Robotics (owner Priya, admin Marcus, member Lena, viewer Tom) and its partner
Helix Labs (owner Omar; Priya is a member). Every REST endpoint is exercised; the answers
to the questions are checked for the facts the fixtures hold and saved for reading.

usage: python e2e/scenario.py FIXTURES OUT_DIR [phase,phase…]
"""

import asyncio
import contextlib
import csv
import io
import json
import os
import re
import sys
import time
import traceback
import uuid
from pathlib import Path

import httpx

BASE = os.environ.get("STRATA_API", "http://localhost:8000")
FIX = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
RUN = uuid.uuid4().hex[:6]
PASSWORD = f"pw-{uuid.uuid4().hex[:12]}"

RESULTS: list[dict] = []
ANSWERS: list[dict] = []
CALLS: list[tuple[str, str, int]] = []
STATE: dict = {"run": RUN}


def check(section: str, name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"section": section, "name": name, "ok": bool(ok), "detail": str(detail)[:600]})
    print(
        ("PASS " if ok else "FAIL ")
        + f"[{section}] {name}"
        + ("" if ok else f" :: {str(detail)[:300]}"),
        flush=True,
    )
    return bool(ok)


def expect(section, name, r: httpx.Response, status: int | tuple = 200):
    statuses = status if isinstance(status, tuple) else (status,)
    check(
        section,
        f"{name} -> {r.status_code}",
        r.status_code in statuses,
        f"wanted {statuses}: {r.text[:400]}",
    )
    return r


async def _record(response: httpx.Response):
    CALLS.append((response.request.method, response.request.url.path, response.status_code))


def client(token: str | None = None) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(
        base_url=BASE, timeout=600, headers=headers, event_hooks={"response": [_record]}
    )


class User:
    def __init__(self, key: str, name: str):
        self.key = key
        self.name = name
        self.email = (
            f"{key}.{RUN}@northwind.strata.dev" if key != "omar" else f"omar.{RUN}@helix.strata.dev"
        )
        self.http = client()
        self.id: str | None = None

    async def get(self, path, **kw):
        return await self.http.get("/api" + path, **kw)

    async def post(self, path, **kw):
        return await self.http.post("/api" + path, **kw)

    async def patch(self, path, **kw):
        return await self.http.patch("/api" + path, **kw)

    async def put(self, path, **kw):
        return await self.http.put("/api" + path, **kw)

    async def delete(self, path, **kw):
        return await self.http.delete("/api" + path, **kw)


def has(text: str, *needles: str) -> list[str]:
    """Needles missing from text (case-insensitive; `a|b` means either)."""
    low = text.lower()
    return [n for n in needles if not any(alt.lower() in low for alt in n.split("|"))]


async def ask(
    user: User,
    slug: str,
    conv: str,
    question: str,
    section: str,
    label: str,
    *,
    need: tuple = (),
    kinds: tuple = (),
    stream: bool = False,
    **body,
) -> dict:
    t0 = time.monotonic()
    events: list[str] = []
    message = None
    if stream:
        async with user.http.stream(
            "POST",
            f"/api/orgs/{slug}/conversations/{conv}/messages",
            json={"content": question, **body},
        ) as r:
            check(
                section,
                f"{label}: stream opens",
                r.status_code == 200,
                await r.aread() if r.status_code != 200 else "",
            )
            event = None
            async for line in r.aiter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                    events.append(event)
                elif line.startswith("data: ") and event in ("done", "stopped"):
                    message = json.loads(line[6:])["message"]
                elif line.startswith("data: ") and event == "error":
                    check(section, f"{label}: no stream error", False, line)
        check(
            section,
            f"{label}: SSE order start→…→done",
            events[:1] == ["start"]
            and events[-1:] == ["done"]
            and "delta" in events
            and "sources" in events
            and "step" in events,
            events[:6] + events[-3:],
        )
    else:
        r = await user.post(
            f"/orgs/{slug}/conversations/{conv}/messages",
            params={"stream": "false"},
            json={"content": question, **body},
        )
        if r.status_code != 200:
            check(section, f"{label}: answered", False, r.text)
            return {}
        message = r.json()["message"]
    elapsed = time.monotonic() - t0
    content = message["content"]
    steps = [s["kind"] for s in message["trace"]]
    record = {
        "label": label,
        "user": user.name,
        "question": question,
        "answer": content,
        "seconds": round(elapsed, 1),
        "steps": [(s["kind"], s["label"]) for s in message["trace"]],
        "sources": [
            (
                s["ref"],
                s["title"],
                s.get("org_slug"),
                s.get("collection_name"),
                s.get("source_kind"),
            )
            for s in message["sources"]
        ],
        "status": message["status"],
        "id": message["id"],
    }
    ANSWERS.append(record)
    check(section, f"{label}: complete", message["status"] == "complete", message["status"])
    missing = has(content, *need)
    check(
        section,
        f"{label}: answer has the facts",
        not missing,
        f"missing {missing} in: {content[:500]}",
    )
    retract = re.search(
        r"(correct|retract)\w* (to )?(my|the|an) (earlier|previous|first)|without (any )?(sources|citations)|gave (them|it|those|that) without",
        content,
        re.I,
    )
    check(
        section,
        f"{label}: no spurious retraction of earlier answers",
        not retract,
        retract and content[max(0, retract.start() - 200) : retract.end() + 200],
    )
    for k in kinds:
        check(section, f"{label}: used {k}", k in steps, steps)
    return message


async def wait_until(desc: str, fn, limit: float = 900, every: float = 3.0):
    deadline = time.monotonic() + limit
    last = None
    while time.monotonic() < deadline:
        last = await fn()
        if last:
            return last
        await asyncio.sleep(every)
    check("wait", desc, False, f"timed out; last={last}")
    return None


async def upload(user: User, slug: str, source_id: str, paths: list[Path]) -> httpx.Response:
    files = [("files", (p.name, p.read_bytes())) for p in paths]
    return await user.post(f"/orgs/{slug}/sources/{source_id}/files", files=files)


async def source_ready(user, slug, source_id, docs: int):
    async def ready():
        s = (await user.get(f"/orgs/{slug}/sources/{source_id}")).json()
        if s["status"] == "error":
            return s
        return s if s["status"] == "ready" and s["document_count"] >= docs else None

    return await wait_until(f"source {source_id} ready", ready)


# ───────────────────────────────────────── phases ─────────────────────────────────────────


async def accounts(U):
    S = "accounts"
    anon = client()
    r = expect(S, "GET /health", await anon.get("/api/health"))
    check(S, "health: LLM configured", r.json()["llm_configured"], r.json())
    expect(S, "connectors need sign-in", await anon.get("/api/connectors"), 401)
    for u in U.values():
        r = expect(
            S,
            f"signup {u.key}",
            await u.post(
                "/auth/signup", json={"email": u.email, "password": PASSWORD, "name": u.name}
            ),
            (200, 201),
        )
        u.id = r.json()["user"]["id"]
    p = U["priya"]
    expect(
        S,
        "duplicate signup",
        await anon.post(
            "/api/auth/signup", json={"email": p.email, "password": PASSWORD, "name": "x"}
        ),
        409,
    )
    expect(
        S,
        "short password",
        await anon.post(
            "/api/auth/signup",
            json={"email": f"x{RUN}@e2e.strata.dev", "password": "short", "name": "x"},
        ),
        422,
    )
    expect(
        S,
        "wrong password",
        await anon.post("/api/auth/login", json={"email": p.email, "password": "wrong-password"}),
        401,
    )
    expect(S, "logout", await p.post("/auth/logout"), 204)
    expect(S, "me after logout", await p.get("/auth/me"), 401)
    expect(S, "login", await p.post("/auth/login", json={"email": p.email, "password": PASSWORD}))
    me = expect(S, "me", await p.get("/auth/me")).json()
    check(S, "me: no orgs yet", me["user"]["email"] == p.email and me["orgs"] == [], me)
    r = expect(S, "GET /models", await p.get("/models"))
    check(
        S,
        "models: haiku available",
        any(m["id"] == "claude-haiku-5-5" and m["available"] for m in r.json()),
        r.json(),
    )
    r = expect(S, "GET /connectors", await p.get("/connectors"))
    check(
        S,
        "connectors catalogue",
        {"upload", "web", "github"} <= {c["kind"] for c in r.json()},
        [c["kind"] for c in r.json()],
    )
    await anon.aclose()


async def orgs(U):
    S = "orgs"
    p, o = U["priya"], U["omar"]
    r = expect(
        S,
        "create Northwind",
        await p.post(
            "/orgs",
            json={
                "name": "Northwind Robotics",
                "slug": f"northwind-{RUN}",
                "description": "Warehouse robots",
            },
        ),
        (200, 201),
    )
    nw = r.json()
    STATE["nw"] = nw["slug"]
    check(S, "creator is owner", nw["role"] == "owner", nw)
    r = expect(
        S,
        "PATCH org settings",
        await p.patch(
            f"/orgs/{nw['slug']}",
            json={"hue": 210, "settings": {"model": "claude-haiku-5-5", "knowledge_graph": True}},
        ),
    )
    check(
        S,
        "settings saved",
        r.json()["settings"]["knowledge_graph"] is True
        and r.json()["effective_model"] == "claude-haiku-5-5",
        r.json()["settings"],
    )
    expect(
        S,
        "unknown model refused",
        await p.patch(f"/orgs/{nw['slug']}", json={"settings": {"model": "gpt-nope"}}),
        422,
    )
    expect(S, "GET org", await p.get(f"/orgs/{nw['slug']}"))
    r = expect(
        S,
        "create Helix",
        await o.post("/orgs", json={"name": "Helix Labs", "slug": f"helix-{RUN}"}),
        (200, 201),
    )
    STATE["hx"] = r.json()["slug"]
    r = expect(S, "scratch org", await p.post("/orgs", json={"name": f"Scratch {RUN}"}), (200, 201))
    scratch = r.json()["slug"]
    expect(S, "non-member can't see org", await o.get(f"/orgs/{nw['slug']}"), 404)
    expect(S, "DELETE scratch org", await p.delete(f"/orgs/{scratch}"), 204)
    expect(S, "deleted org gone", await p.get(f"/orgs/{scratch}"), 404)
    r = expect(S, "GET /orgs", await p.get("/orgs"))
    check(
        S, "orgs list has Northwind only", [x["slug"] for x in r.json()] == [nw["slug"]], r.json()
    )


async def members(U):
    S = "members"
    p, o = U["priya"], U["omar"]
    nw, hx = STATE["nw"], STATE["hx"]
    anon = client()
    roles = {"marcus": "admin", "lena": "member", "tom": "viewer", "jules": "member"}
    for key, role in roles.items():
        r = expect(
            S,
            f"invite {key} as {role}",
            await p.post(f"/orgs/{nw}/invites", json={"email": U[key].email, "role": role}),
            (200, 201),
        )
        token = r.json()["token"]
        info = expect(
            S, f"invite {key} preview (no auth)", await anon.get(f"/api/invites/{token}")
        ).json()
        check(
            S,
            f"invite {key} preview fields",
            info["role"] == role and info["org"]["slug"] == nw and not info["expired"],
            info,
        )
        expect(
            S,
            f"wrong user can't accept {key}'s invite",
            await o.post(f"/invites/{token}/accept"),
            403,
        )
        expect(S, f"{key} accepts", await U[key].post(f"/invites/{token}/accept"))
        if key == "jules":
            expect(S, "used invite refused", await U[key].post(f"/invites/{token}/accept"), 400)
    r = expect(
        S,
        "invite Priya to Helix",
        await o.post(f"/orgs/{hx}/invites", json={"email": p.email, "role": "member"}),
        (200, 201),
    )
    expect(S, "Priya joins Helix", await p.post(f"/invites/{r.json()['token']}/accept"))
    r = expect(
        S,
        "invite to revoke",
        await p.post(
            f"/orgs/{nw}/invites", json={"email": f"nobody.{RUN}@e2e.strata.dev", "role": "member"}
        ),
        (200, 201),
    )
    expect(S, "list invites", await p.get(f"/orgs/{nw}/invites"))
    expect(S, "revoke invite", await p.delete(f"/orgs/{nw}/invites/{r.json()['id']}"), 204)
    expect(
        S,
        "member can't invite",
        await U["lena"].post(
            f"/orgs/{nw}/invites", json={"email": "z@e2e.strata.dev", "role": "member"}
        ),
        403,
    )
    ms = expect(S, "list members", await p.get(f"/orgs/{nw}/members")).json()
    check(S, "5 members", len(ms) == 5, [m["user"]["email"] for m in ms])
    t = U["tom"]
    r = expect(
        S, "promote Tom", await p.patch(f"/orgs/{nw}/members/{t.id}", json={"role": "member"})
    )
    expect(
        S,
        "demote Tom back to viewer",
        await p.patch(f"/orgs/{nw}/members/{t.id}", json={"role": "viewer"}),
    )
    expect(
        S,
        "last owner can't be demoted",
        await p.patch(f"/orgs/{nw}/members/{p.id}", json={"role": "admin"}),
        409,
    )
    expect(S, "remove Jules", await p.delete(f"/orgs/{nw}/members/{U['jules'].id}"), 204)
    expect(S, "Jules lost access", await U["jules"].get(f"/orgs/{nw}"), 404)
    await anon.aclose()


async def collections(U):
    S = "collections"
    p, nw = U["priya"], STATE["nw"]
    cols = expect(S, "list collections", await p.get(f"/orgs/{nw}/collections")).json()
    check(S, "default General", [c["name"] for c in cols] == ["General"], cols)
    spec = {
        "handbook": {
            "name": "Company Handbook",
            "visibility": "org",
            "glyph": "book",
            "description": "Policies everyone follows",
        },
        "engineering": {"name": "Engineering", "visibility": "org", "glyph": "code"},
        "vendors": {
            "name": "Vendors & Contracts",
            "visibility": "restricted",
            "glyph": "chart",
            "allowed_user_ids": [U["lena"].id],
        },
        "leadership": {
            "name": "Leadership",
            "visibility": "restricted",
            "glyph": "shield",
            "allowed_user_ids": [],
        },
        "scratch": {"name": "Scratch", "visibility": "org"},
    }
    STATE["col"] = {}
    for key, body in spec.items():
        r = expect(
            S, f"create {key}", await p.post(f"/orgs/{nw}/collections", json=body), (200, 201)
        )
        STATE["col"][key] = r.json()["id"]
    expect(
        S,
        "name taken",
        await p.post(f"/orgs/{nw}/collections", json={"name": "Engineering", "visibility": "org"}),
        409,
    )
    expect(
        S,
        "bad glyph",
        await p.post(f"/orgs/{nw}/collections", json={"name": "G", "glyph": "rocket"}),
        422,
    )
    expect(
        S,
        "non-member grant refused",
        await p.post(
            f"/orgs/{nw}/collections",
            json={"name": "X", "visibility": "restricted", "allowed_user_ids": [U["omar"].id]},
        ),
        400,
    )
    expect(
        S,
        "member can't create",
        await U["lena"].post(f"/orgs/{nw}/collections", json={"name": "Mine"}),
        403,
    )
    expect(
        S,
        "PATCH collection",
        await p.patch(
            f"/orgs/{nw}/collections/{STATE['col']['engineering']}",
            json={"description": "Runbooks, postmortems, architecture"},
        ),
    )
    names = lambda u: [c["name"] for c in u]  # noqa: E731
    lena = expect(S, "Lena's collections", await U["lena"].get(f"/orgs/{nw}/collections")).json()
    tom = expect(S, "Tom's collections", await U["tom"].get(f"/orgs/{nw}/collections")).json()
    marcus = expect(
        S, "Marcus's collections", await U["marcus"].get(f"/orgs/{nw}/collections")
    ).json()
    check(
        S,
        "Lena sees Vendors, not Leadership",
        "Vendors & Contracts" in names(lena) and "Leadership" not in names(lena),
        names(lena),
    )
    check(
        S,
        "Tom sees neither restricted",
        not {"Vendors & Contracts", "Leadership"} & set(names(tom)),
        names(tom),
    )
    check(
        S,
        "admin sees all",
        {"Vendors & Contracts", "Leadership"} <= set(names(marcus)),
        names(marcus),
    )
    o, hx = U["omar"], STATE["hx"]
    r = expect(
        S,
        "Helix collection",
        await o.post(f"/orgs/{hx}/collections", json={"name": "Partner Docs", "visibility": "org"}),
        (200, 201),
    )
    STATE["col"]["helix"] = r.json()["id"]


async def sources(U):
    S = "sources"
    p, nw = U["priya"], STATE["nw"]
    STATE["src"] = {}
    folders = {
        "handbook": "northwind/handbook",
        "engineering": "northwind/engineering",
        "vendors": "northwind/vendors",
        "leadership": "northwind/leadership",
    }
    for key in folders:
        r = expect(
            S,
            f"create {key} upload source",
            await p.post(
                f"/orgs/{nw}/sources",
                json={
                    "kind": "upload",
                    "name": f"{key.title()} uploads",
                    "collection_id": STATE["col"][key],
                    "config": {},
                },
            ),
            (200, 201),
        )
        STATE["src"][key] = r.json()["id"]
    expect(
        S,
        "viewer can't upload",
        await upload(
            U["tom"],
            nw,
            STATE["src"]["handbook"],
            [FIX / "northwind/handbook/onboarding-checklist.txt"],
        ),
        403,
    )
    bad = await U["lena"].post(
        f"/orgs/{nw}/sources/{STATE['src']['engineering']}/files",
        files=[("files", ("evil.exe", b"MZ"))],
    )
    expect(S, "unsupported type refused", bad, 400)
    expect(
        S,
        "member can't create a source",
        await U["lena"].post(
            f"/orgs/{nw}/sources",
            json={
                "kind": "upload",
                "name": "x",
                "collection_id": STATE["col"]["engineering"],
                "config": {},
            },
        ),
        403,
    )
    expected = {}
    for key, folder in folders.items():
        files = sorted((FIX / folder).iterdir())
        uploader = U["lena"] if key == "engineering" else p  # a member may upload
        r = expect(
            S,
            f"upload {len(files)} files to {key}",
            await upload(uploader, nw, STATE["src"][key], files),
        )
        check(S, f"{key}: all accepted", r.json()["accepted"] == len(files), r.json())
        expected[key] = len(files)
    r = expect(
        S,
        "web source",
        await p.post(
            f"/orgs/{nw}/sources",
            json={
                "kind": "web",
                "name": "Example site",
                "collection_id": STATE["col"]["scratch"],
                "config": {"url": "https://example.com", "max_pages": 1, "max_depth": 0},
            },
        ),
        (200, 201),
    )
    STATE["src"]["web"] = r.json()["id"]
    o, hx = U["omar"], STATE["hx"]
    r = expect(
        S,
        "Helix upload source",
        await o.post(
            f"/orgs/{hx}/sources",
            json={
                "kind": "upload",
                "name": "Partner uploads",
                "collection_id": STATE["col"]["helix"],
                "config": {},
            },
        ),
        (200, 201),
    )
    STATE["src"]["helix"] = r.json()["id"]
    expect(
        S,
        "Helix upload",
        await upload(o, hx, STATE["src"]["helix"], sorted((FIX / "helix").iterdir())),
    )

    for key, n in expected.items():
        s = await source_ready(p, nw, STATE["src"][key], n)
        check(
            S,
            f"{key} indexed ({n} docs)",
            s and s["status"] == "ready" and s["document_count"] == n,
            s and {k: s[k] for k in ("status", "error", "document_count", "chunk_count")},
        )
    s = await source_ready(o, hx, STATE["src"]["helix"], 2)
    check(S, "Helix indexed", s and s["status"] == "ready", s)
    s = await source_ready(p, nw, STATE["src"]["web"], 1)
    check(S, "web page crawled", s and s["status"] == "ready" and s["document_count"] == 1, s)
    expect(
        S,
        "PATCH web source",
        await p.patch(
            f"/orgs/{nw}/sources/{STATE['src']['web']}",
            json={"name": "Example.com", "sync_interval_minutes": 1440},
        ),
    )
    r = expect(S, "sync web source", await p.post(f"/orgs/{nw}/sources/{STATE['src']['web']}/sync"))
    check(S, "sync queued", r.json()["status"] in ("queued", "syncing"), r.json()["status"])
    s = await source_ready(p, nw, STATE["src"]["web"], 1)
    expect(S, "list sources", await p.get(f"/orgs/{nw}/sources"))
    docs = expect(
        S,
        "source documents",
        await p.get(
            f"/orgs/{nw}/sources/{STATE['src']['vendors']}/documents", params={"q": "contract"}
        ),
    ).json()
    check(S, "source documents filter", docs["total"] == 5, [d["title"] for d in docs["items"]])
    expect(S, "DELETE web source", await p.delete(f"/orgs/{nw}/sources/{STATE['src']['web']}"), 204)


async def documents(U):
    S = "documents"
    p, nw = U["priya"], STATE["nw"]
    page = expect(
        S, "list documents", await p.get(f"/orgs/{nw}/documents", params={"limit": 100})
    ).json()
    STATE["docs"] = {d["title"]: d for d in page["items"]}
    check(S, "33 documents", page["total"] == 33, page["total"])
    print("   titles:", sorted(STATE["docs"]), flush=True)

    def doc(fragment):
        return next((d for t, d in STATE["docs"].items() if fragment.lower() in t.lower()), None)

    STATE["doc"] = doc
    probes = {  # parser check: facts that only that format's parser gets out
        "information security": ["4 hours", "SEV1"],  # PDF via LibreOffice
        "amendment": ["ninety (90) days|90 days", "Daniel Reyes"],  # PNG, OCR
        "board update": ["Berlin", "18 hires"],  # PPTX
        "vendor register": ["Arrow Electronics", "Customer Data Access"],  # XLSX
        "zendesk renewal|your zendesk renewal": ["12%", "71,680"],  # EML
        "benefits": ["Spring Health", "$1,500"],  # HTML
        "travel": ["Head of Finance", "$25,000"],  # DOCX with a table
        "team directory|team-directory": ["Amara Okafor", "Head of Finance"],  # CSV
    }
    for frag, needles in probes.items():
        d = next((doc(f) for f in frag.split("|") if doc(f)), None)
        if not check(S, f"found '{frag}'", d is not None, sorted(STATE["docs"])):
            continue
        full = expect(
            S, f"GET document '{d['title']}'", await p.get(f"/orgs/{nw}/documents/{d['id']}")
        ).json()
        miss = has(full["content"], *needles)
        check(
            S,
            f"parsed '{d['title']}' ({d['mime']})",
            not miss,
            f"missing {miss}: {full['content'][:300]}",
        )
    tom = expect(
        S, "Tom's documents", await U["tom"].get(f"/orgs/{nw}/documents", params={"limit": 100})
    ).json()
    check(S, "Tom sees only org-wide docs", tom["total"] == page["total"] - 8 - 3, tom["total"])
    # Weak pages: the chart image is mostly a picture, and vision parsing is off.
    chart = doc("headcount")
    check(
        S,
        "chart image flagged as a weak page",
        chart and chart["parse"] == {"pages": 1, "weak_pages": [1], "vision_pages": None},
        chart and chart["parse"],
    )
    check(
        S,
        "a clean scan isn't weak",
        doc("amendment")["parse"]["weak_pages"] == [],
        doc("amendment")["parse"],
    )
    check(
        S,
        "pageless formats have no parse",
        doc("employee handbook")["parse"] is None,
        doc("employee handbook")["parse"],
    )
    weak = expect(
        S, "weak=true filter", await p.get(f"/orgs/{nw}/documents", params={"weak": "true"})
    ).json()
    check(
        S,
        "only the chart may be missing text",
        [d["title"] for d in weak["items"]] == [chart["title"]],
        [d["title"] for d in weak["items"]],
    )
    srcs = {
        x["id"]: x for x in expect(S, "sources list", await p.get(f"/orgs/{nw}/sources")).json()
    }
    check(
        S,
        "weak count on the Leadership source only",
        srcs[STATE["src"]["leadership"]]["weak_document_count"] == 1
        and sum(x["weak_document_count"] for x in srcs.values()) == 1,
        {k: x["weak_document_count"] for k, x in srcs.items()},
    )
    weak_src = expect(
        S,
        "weak filter on a source",
        await p.get(
            f"/orgs/{nw}/sources/{STATE['src']['leadership']}/documents", params={"weak": "true"}
        ),
    ).json()
    check(S, "source filter finds the chart", weak_src["total"] == 1, weak_src)
    vendor_doc = doc("vendor register")
    expect(
        S,
        "Tom can't read a restricted doc",
        await U["tom"].get(f"/orgs/{nw}/documents/{vendor_doc['id']}"),
        404,
    )
    expect(
        S,
        "Lena reads a Vendors doc",
        await U["lena"].get(f"/orgs/{nw}/documents/{vendor_doc['id']}"),
    )
    r = expect(
        S,
        "filter by collection",
        await p.get(
            f"/orgs/{nw}/documents",
            params={"collection_id": STATE["col"]["engineering"], "sort": "title"},
        ),
    )
    check(S, "engineering has 16", r.json()["total"] == 16, r.json()["total"])
    r = expect(S, "title search", await p.get(f"/orgs/{nw}/documents", params={"q": "postmortem"}))
    check(S, "14 postmortems", r.json()["total"] == 14, r.json()["total"])


async def search(U):
    S = "search"
    p, nw, hx = U["priya"], STATE["nw"], STATE["hx"]
    r = expect(
        S,
        "search",
        await p.post(
            f"/orgs/{nw}/search",
            json={"query": "how fast must a lost laptop be reported", "limit": 5},
        ),
    ).json()
    check(
        S,
        "security policy ranks",
        any("security" in x["title"].lower() for x in r["results"][:3]),
        [x["title"] for x in r["results"]],
    )
    r = expect(
        S,
        "Tom searches vendor terms",
        await U["tom"].post(
            f"/orgs/{nw}/search", json={"query": "Zendesk renewal price increase notice period"}
        ),
    ).json()
    check(
        S,
        "no restricted results for Tom",
        all(
            x["collection_name"] not in ("Vendors & Contracts", "Leadership") for x in r["results"]
        ),
        [(x["title"], x["collection_name"]) for x in r["results"]],
    )
    r = expect(
        S,
        "cross-org search",
        await p.post(
            f"/orgs/{nw}/search",
            json={"query": "lost device reporting deadline", "org_slugs": [nw, hx]},
        ),
    ).json()
    check(
        S,
        "both orgs in results",
        {nw, hx} <= {x["org_slug"] for x in r["results"]},
        [(x["title"], x["org_slug"]) for x in r["results"]],
    )
    r = expect(
        S,
        "Lena can't search Helix",
        await U["lena"].post(
            f"/orgs/{nw}/search", json={"query": "lost device", "org_slugs": [nw, hx]}
        ),
    ).json()
    check(
        S,
        "Lena gets Northwind only",
        {x["org_slug"] for x in r["results"]} <= {nw},
        {x["org_slug"] for x in r["results"]},
    )
    r = expect(
        S,
        "nonsense search",
        await U["tom"].post(f"/orgs/{nw}/search", json={"query": "qzxv wlorptastic frumbulator"}),
    ).json()
    STATE["nonsense_hits"] = len(r["results"])
    print("   nonsense hits:", len(r["results"]), flush=True)


async def records(U):
    S = "records"
    p, nw = U["priya"], STATE["nw"]
    found = {}
    for key in ("vendors", "handbook", "engineering"):
        r = expect(
            S,
            f"suggest in {key}",
            await p.post(f"/orgs/{nw}/collections/{STATE['col'][key]}/record-schemas/suggest"),
        )
        found[key] = r.json()
        print(
            f"   {key} suggestions:",
            [
                (
                    s["name"],
                    s["origin"],
                    [f["name"] for f in s["fields"]],
                    s["stats"].get("records") or s["stats"].get("documents"),
                )
                for s in r.json()
            ],
            flush=True,
        )
    expect(
        S,
        "member can't suggest",
        await U["lena"].post(
            f"/orgs/{nw}/collections/{STATE['col']['vendors']}/record-schemas/suggest"
        ),
        403,
    )

    def pick(key, *fields):
        return next(
            (
                s
                for s in found[key]
                if all(any(f in x["name"] for x in s["fields"]) for f in fields)
            ),
            None,
        )

    register = pick("vendors", "vendor", "renewal")
    directory = pick("handbook", "email", "title")
    incidents = pick("engineering", "severity")
    for name, s in (
        ("vendor register", register),
        ("team directory", directory),
        ("postmortems", incidents),
    ):
        check(
            S,
            f"suggested a schema for the {name}",
            s is not None,
            [
                x["name"]
                for x in found.get("vendors", [])
                + found.get("handbook", [])
                + found.get("engineering", [])
            ],
        )
    if not (register and directory and incidents):
        return
    STATE["schemas"] = {"register": register, "directory": directory, "incidents": incidents}
    expect(
        S,
        "PATCH label",
        await p.patch(
            f"/orgs/{nw}/record-schemas/{register['id']}", json={"label": "Vendor register"}
        ),
    )
    for s in (register, directory, incidents):
        r = expect(
            S,
            f"activate {s['name']}",
            await p.patch(f"/orgs/{nw}/record-schemas/{s['id']}", json={"status": "active"}),
        )
    contract = {
        "collection_id": STATE["col"]["vendors"],
        "name": "contract_terms",
        "label": "Contract terms",
        "description": "Renewal and termination terms of each vendor contract, as stated in contract summaries.",
        "key_field": "vendor",
        "fields": [
            {"name": "vendor", "label": "Vendor", "type": "text"},
            {"name": "auto_renew", "label": "Renews automatically", "type": "boolean"},
            {
                "name": "notice_days",
                "label": "Notice period (days)",
                "type": "number",
                "description": "Days of notice needed for non-renewal or termination",
            },
            {
                "name": "price_cap_pct",
                "label": "Renewal price cap (%)",
                "type": "number",
                "description": "Largest allowed price increase on renewal, in percent",
            },
            {
                "name": "termination_for_convenience",
                "label": "Termination for convenience",
                "type": "boolean",
            },
            {"name": "governing_law", "label": "Governing law", "type": "text"},
        ],
    }
    r = expect(
        S, "create a prose schema", await p.post(f"/orgs/{nw}/record-schemas", json=contract), 201
    )
    terms = r.json()
    STATE["schemas"]["terms"] = terms
    check(
        S,
        "prose schema origin llm",
        terms["origin"] == "llm" and terms["status"] == "suggested",
        terms,
    )
    expect(
        S, "duplicate schema name", await p.post(f"/orgs/{nw}/record-schemas", json=contract), 409
    )
    expect(
        S,
        "extract inactive refused",
        await p.post(f"/orgs/{nw}/record-schemas/{terms['id']}/extract"),
        400,
    )
    expect(
        S,
        "activate contract_terms",
        await p.patch(f"/orgs/{nw}/record-schemas/{terms['id']}", json={"status": "active"}),
    )
    for key, s in STATE["schemas"].items():

        async def done(s=s):
            d = (await p.get(f"/orgs/{nw}/record-schemas/{s['id']}")).json()
            return d if not d["extracting"] and d.get("extracted_at") else None

        d = await wait_until(f"{key} extracted", done, limit=900)
        if d:
            print(f"   {key}: stats={d['stats']} failures={d['failures']}", flush=True)
            check(S, f"{key}: no failures", not d["failures"], d["failures"])
            STATE["schemas"][key] = d
    st = STATE["schemas"]
    check(
        S,
        "30 vendor records",
        st["register"]["stats"].get("records") == 30,
        st["register"]["stats"],
    )
    check(S, "22 people", st["directory"]["stats"].get("records") == 22, st["directory"]["stats"])
    check(
        S, "14 incidents", st["incidents"]["stats"].get("records") == 14, st["incidents"]["stats"]
    )
    check(
        S,
        "postmortems named by their titles, keyed by date",
        (st["incidents"]["name"], st["incidents"]["label"], st["incidents"]["key_field"])
        == ("postmortem", "Postmortem", "date"),
        (st["incidents"]["name"], st["incidents"]["key_field"]),
    )
    inc_fields = {f["name"]: f["type"] for f in st["incidents"]["fields"]}
    check(S, "incident duration inferred as text", inc_fields.get("duration") == "text", inc_fields)
    r = await p.post(
        f"/orgs/{nw}/records/query",
        json={
            "schema": st["incidents"]["name"],
            "filters": [{"field": "severity", "op": "eq", "value": "SEV1"}],
        },
    )
    check(
        S,
        "SSO outage keeps its duration",
        "1 hour 25 minutes" in json.dumps(r.json()),
        r.text[:500],
    )
    reg = st["register"]
    fields = {f["name"]: f for f in reg["fields"]}
    print("   register fields:", {k: v["type"] for k, v in fields.items()}, flush=True)
    check(
        S,
        "owners are text, not a fixed choice",
        fields.get("business_owner", {}).get("type") == "text",
        fields.get("business_owner"),
    )

    def fname(*parts):
        return next((n for n in fields if all(x in n for x in parts)), None)

    soc, data, status, cat = (
        fname("soc"),
        fname("customer", "data"),
        fname("status"),
        fname("category"),
    )
    q = {
        "schema": reg["name"],
        "filters": [
            {"field": data, "op": "eq", "value": "Yes"},
            {"field": status, "op": "eq", "value": "Active"},
            {"field": soc, "op": "lt", "value": "2027-03-31"},
        ],
        "sort": soc,
    }
    r = expect(
        S,
        "query: SOC 2 expiring, customer data, active",
        await p.post(f"/orgs/{nw}/records/query", json=q),
    )
    got = sorted(row["values"][fname("vendor")] for row in r.json()["rows"])
    check(
        S,
        "exactly the 8 vendors",
        got
        == sorted(
            ["Datadog", "Sentry", "Snowflake", "Zendesk", "Okta", "Twilio", "Vanta", "HubSpot"]
        ),
        got,
    )
    row = r.json()["rows"][0]
    check(
        S, "rows carry provenance and document", row["provenance"] and row["document"]["title"], row
    )
    r = expect(
        S,
        "count mode",
        await p.post(
            f"/orgs/{nw}/records/query",
            json={
                "schema": reg["name"],
                "mode": "count",
                "filters": [{"field": status, "op": "eq", "value": "active"}],
            },
        ),
    )
    check(S, "28 active", r.json()["total"] == 28, r.json()["total"])
    r = expect(
        S,
        "group mode",
        await p.post(
            f"/orgs/{nw}/records/query",
            json={
                "schema": reg["name"],
                "mode": "group",
                "group_by": cat,
                "filters": [{"field": status, "op": "eq", "value": "Active"}],
            },
        ),
    )
    groups = {g["value"]: g["count"] for g in r.json()["groups"]}
    check(
        S,
        "Security is the biggest category (4)",
        groups.get("Security") == 4 and max(groups.values()) == 4,
        groups,
    )
    expect(
        S,
        "bad operator",
        await p.post(
            f"/orgs/{nw}/records/query",
            json={
                "schema": reg["name"],
                "filters": [{"field": soc, "op": "contains", "value": "x"}],
            },
        ),
        400,
    )
    expect(
        S, "unknown schema", await p.post(f"/orgs/{nw}/records/query", json={"schema": "nope"}), 404
    )
    expect(
        S,
        "Tom can't query vendors",
        await U["tom"].post(f"/orgs/{nw}/records/query", json={"schema": reg["name"]}),
        404,
    )
    r = expect(S, "Tom's record types", await U["tom"].get(f"/orgs/{nw}/record-schemas")).json()
    check(
        S,
        "Tom sees directory + incidents only",
        {s["name"] for s in r} == {st["directory"]["name"], st["incidents"]["name"]},
        [s["name"] for s in r],
    )
    expect(
        S,
        "list schemas by collection",
        await p.get(
            f"/orgs/{nw}/record-schemas", params={"collection_id": STATE["col"]["vendors"]}
        ),
    )
    rid = row["id"]
    expect(S, "GET record", await p.get(f"/orgs/{nw}/records/{rid}"))
    expect(S, "Tom can't GET a vendor record", await U["tom"].get(f"/orgs/{nw}/records/{rid}"), 404)
    r = expect(
        S,
        "resolve Head of Finance",
        await U["tom"].post(f"/orgs/{nw}/records/resolve", json={"role": "Head of Finance"}),
    )
    check(
        S,
        "Amara Okafor",
        any("Amara Okafor" in json.dumps(m["record"]["values"]) for m in r.json()),
        r.json()[:2],
    )
    r = expect(
        S,
        "resolve Security Lead",
        await p.post(f"/orgs/{nw}/records/resolve", json={"role": "security lead"}),
    )
    check(
        S,
        "Ines Carvalho",
        any("Ines Carvalho" in json.dumps(m["record"]["values"]) for m in r.json()),
        [m["record"]["values"] for m in r.json()[:3]],
    )
    terms = st["terms"]
    r = expect(
        S,
        "contract terms rows",
        await p.post(f"/orgs/{nw}/records/query", json={"schema": "contract_terms", "limit": 300}),
    )
    by_doc = {}
    for x in r.json()["rows"]:
        by_doc.setdefault(x["document"]["title"], []).append(x)
    register_rows = next((v for t, v in by_doc.items() if "register" in t.lower()), [])
    check(
        S,
        "register read row by row for contract_terms (30)",
        len(register_rows) == 30
        and all("termination_for_convenience" not in x["values"] for x in register_rows),
        (len(register_rows), [x["values"] for x in register_rows[:3]]),
    )
    rows = {
        x["values"].get("vendor"): x["values"]
        for t, v in by_doc.items()
        if t.startswith("Contract summary")
        for x in v
    }
    print(
        "   contract_terms:",
        json.dumps({k: rows[k] for k in sorted(rows, key=str)[:40]}, default=str)[:1500],
        flush=True,
    )
    for vendor, want in {
        "Zendesk": {"price_cap_pct": 7, "notice_days": 45, "auto_renew": True},
        "Arrow Electronics": {"termination_for_convenience": True, "notice_days": 180},
        "Okta": {"price_cap_pct": 3},
        "Datadog": {"price_cap_pct": 5, "notice_days": 60},
    }.items():
        v = next(
            (vals for k, vals in rows.items() if k and vendor.split()[0].lower() in str(k).lower()),
            {},
        )
        wrong = {f: (v.get(f), x) for f, x in want.items() if v.get(f) != x}
        check(S, f"LangExtract read {vendor}'s terms", not wrong, wrong)
    r = expect(
        S,
        "termination for convenience",
        await p.post(
            f"/orgs/{nw}/records/query",
            json={
                "schema": "contract_terms",
                "filters": [{"field": "termination_for_convenience", "op": "eq", "value": True}],
            },
        ),
    )
    check(
        S,
        "only Arrow terminates for convenience",
        sorted({x["values"]["vendor"] for x in r.json()["rows"]})
        in (["Arrow Electronics"], ["Arrow"]),
        [x["values"] for x in r.json()["rows"]],
    )
    expect(
        S,
        "re-extract (force)",
        await p.post(f"/orgs/{nw}/record-schemas/{reg['id']}/extract", params={"force": "true"}),
        202,
    )
    leftovers = [
        s for k in found for s in found[k] if s["id"] not in {x["id"] for x in st.values()}
    ]
    if leftovers:
        expect(
            S,
            f"DELETE unused suggestion {leftovers[0]['name']}",
            await p.delete(f"/orgs/{nw}/record-schemas/{leftovers[0]['id']}"),
            204,
        )


async def graph(U):
    S = "graph"
    p, nw = U["priya"], STATE["nw"]
    r = expect(S, "rebuild", await p.post(f"/orgs/{nw}/graph/rebuild"), 202)
    expect(S, "member can't rebuild", await U["lena"].post(f"/orgs/{nw}/graph/rebuild"), 403)

    async def settled():
        g = (await p.get(f"/orgs/{nw}/graph")).json()
        return g if g["documents_pending"] == 0 and g["documents_extracted"] > 0 else None

    g = await wait_until("graph extracted", settled, limit=1500, every=10)
    print("   graph:", g, flush=True)
    check(S, "graph built", g and g["entities"] > 20 and g["relations"] > 10, g)
    r = expect(
        S, "entities", await p.get(f"/orgs/{nw}/graph/entities", params={"q": "ClickHouse"})
    ).json()
    check(
        S,
        "ClickHouse is an entity",
        any("clickhouse" in e["name"].lower() for e in r["items"]),
        [e["name"] for e in r["items"][:10]],
    )
    if r["items"]:
        e = expect(
            S, "entity detail", await p.get(f"/orgs/{nw}/graph/entities/{r['items'][0]['id']}")
        ).json()
        check(S, "entity has relations", e["relation_count"] > 0, e)
    r = expect(
        S,
        "Tom's entities",
        await U["tom"].get(f"/orgs/{nw}/graph/entities", params={"q": "Zendesk", "limit": 50}),
    ).json()
    leaked = [
        e
        for e in r["items"]
        if set(e["document_ids"])
        & {
            d["id"]
            for t, d in STATE["docs"].items()
            if d["collection_id"] in (STATE["col"]["vendors"], STATE["col"]["leadership"])
        }
    ]
    check(S, "graph hides restricted documents from Tom", not leaked, leaked[:2])


async def conversation(user, slug, **body) -> str:
    r = await user.post(f"/orgs/{slug}/conversations", json=body)
    check("chat", f"new conversation for {user.key}", r.status_code in (200, 201), r.text)
    return r.json()["id"]


async def chat(U):
    S = "chat"
    p, m, lena, t = U["priya"], U["marcus"], U["lena"], U["tom"]
    nw, hx = STATE["nw"], STATE["hx"]
    A = STATE["answers"] = {}
    c = await conversation(p, nw)
    A["soc2"] = await ask(
        p,
        nw,
        c,
        "Which active vendors that can access customer data have a SOC 2 Type II report expiring before March 31, 2027? List each with its business owner and expiry date.",
        S,
        "Q1 records filter",
        stream=True,
        need=("Datadog", "Sentry", "Snowflake", "Zendesk", "Okta", "Twilio", "Vanta", "HubSpot"),
        kinds=("records",),
    )
    check(
        S,
        "Q1 read each owner from its row",
        "tall" not in A["soc2"].get("content", "").lower()
        and A["soc2"].get("content", "").count("Kenji Watanabe") >= 3,
        A["soc2"].get("content", "")[-400:],
    )
    A["categories"] = await ask(
        p,
        nw,
        c,
        "How many active vendors do we have in each category, how many in total, and which category has the most?",
        S,
        "Q2 records group",
        need=("Security", "28|twenty-eight"),
        kinds=("records",),
    )
    c2 = await conversation(p, nw, mode="deep")
    A["renewals"] = await ask(
        p,
        nw,
        c2,
        "Which auto-renewing vendor contracts renew before February 1, 2027, and what is the last day we can give notice for each?",
        S,
        "Q3 deep date math",
        need=(
            "Datadog",
            "Sentry",
            "Zendesk",
            "Expensify",
            "Arrow",
            "December 1|Dec 1|2026-12-01| 1 Dec| 1 December",
            "December 10|Dec 10|2026-12-10|10 Dec|10 December",
        ),
        kinds=("records",),
    )
    A["zendesk"] = await ask(
        p,
        nw,
        c,
        "Zendesk emailed us about a price increase at renewal. Is that increase allowed under our contract, and by when do we have to give notice if we decide not to renew?",
        S,
        "Q4 email vs contract",
        need=("12%", "7%", "December 1|Dec 1|2026-12-01| 1 Dec| 1 December"),
    )
    c3 = await conversation(p, nw, scope_org_slugs=[nw, hx])
    A["atlas"] = await ask(
        p,
        nw,
        c3,
        "A Northwind engineer working on Project Atlas lost a laptop that had Helix data on it. Who do they have to notify and by when?",
        S,
        "Q5 cross-org",
        need=(
            "1 hour|one hour|one (1) hour",
            "soc@helixlabs.test",
            "4 hours|four hours|security@northwind.test",
        ),
    )
    orgs_cited = {s.get("org_slug") for s in A["atlas"].get("sources", [])}
    check(S, "Q5 cites both orgs", {nw, hx} <= orgs_cited, orgs_cited)
    A["sponsor"] = await ask(
        p,
        nw,
        c2,
        "I want to sponsor RoboCon 2027 for $12,000. Who has to approve it under our policies, and what's their email address?",
        S,
        "Q6 role multi-hop",
        need=("Amara Okafor", "amara.okafor@northwind.test"),
    )
    cm = await conversation(m, nw)
    A["customer_detected"] = await ask(
        m,
        nw,
        cm,
        "Which incidents this year were first reported by customers rather than caught by our own monitoring? For each one, what was the root cause?",
        S,
        "Q7 scan",
        need=(
            "Brightline",
            "Kestrel",
            "Northpoint",
            "SendGrid|sender pool|notification",
            "feature flag",
        ),
        kinds=("scan",),
    )
    A["laptop"] = await ask(
        m,
        nw,
        cm,
        "How quickly do I need to report a lost laptop, and to whom?",
        S,
        "Q8 conflicting policies",
        need=("4 hours|four hours", "security@northwind.test"),
    )
    A["sev1"] = await ask(
        m,
        nw,
        cm,
        "How many SEV1 incidents have we had this year, and what were they?",
        S,
        "Q9 records count",
        need=("two|2 SEV1|2 incidents", "sign-on|SSO|login", "Reno|warehouse|stopped"),
        kinds=("records",),
    )
    A["deps"] = await ask(
        m,
        nw,
        cm,
        "What does the fleet-control service depend on, and which of those dependencies has caused an incident this year?",
        S,
        "Q9b graph + search",
        need=("PostgreSQL|RDS", "Redis", "Kafka"),
    )
    cl = await conversation(lena, nw)
    A["escalate"] = await ask(
        lena,
        nw,
        cl,
        "If the secondary on-call can't fix a SEV1 within 30 minutes, who do I escalate to, and how do I reach them?",
        S,
        "Q16 runbook + directory",
        need=("Kenji Watanabe", "kenji.watanabe@northwind.test"),
    )
    A["ocr"] = await ask(
        lena,
        nw,
        cl,
        "When did Snowflake's notice-period amendment take effect, what did it change, and who signed it for us?",
        S,
        "Q13 OCR",
        need=(
            "September 1, 2026|2026-09-01|1 September 2026|1 Sep 2026",
            "90|ninety",
            "Daniel Reyes",
        ),
    )
    A["germany"] = await ask(
        lena,
        nw,
        cl,
        "What is the parental leave policy for employees in our Germany office?",
        S,
        "Q11 gap",
    )
    A["l5"] = await ask(
        lena,
        nw,
        cl,
        "What is the salary range for an L5 staff engineer?",
        S,
        "Q12 restricted (before access)",
    )
    check(
        S,
        "Q12: no salary leaked to Lena",
        "195,000" not in A["l5"].get("content", "") and "240,000" not in A["l5"].get("content", ""),
        A["l5"].get("content"),
    )
    ct = await conversation(t, nw)
    A["tom_vendors"] = await ask(
        t,
        nw,
        ct,
        "Which of our vendors have access to customer data?",
        S,
        "Q14 viewer, restricted data",
    )
    restricted = {"Vendors & Contracts", "Leadership"}
    check(
        S,
        "Q14: no restricted sources for Tom",
        not {s.get("collection_name") for s in A["tom_vendors"].get("sources", [])} & restricted,
        A["tom_vendors"].get("sources"),
    )
    check(
        S,
        "Q14: no register facts leaked",
        not any(
            v in A["tom_vendors"].get("content", "")
            for v in ("Twilio", "HubSpot", "Snowflake", "Gusto", "Flexport")
        ),
        A["tom_vendors"].get("content"),
    )
    A["pto"] = await ask(
        t,
        nw,
        ct,
        "How many PTO days do I get, and how many can I carry over into next year?",
        S,
        "Q15 simple",
        need=("25|twenty-five", "5 |five|5 days|5 unused", "March 31|31 March|31 Mar"),
    )
    A["benefits"] = await ask(
        t,
        nw,
        ct,
        "What's the yearly learning budget, and how many therapy sessions are covered?",
        S,
        "Q21 HTML",
        need=("1,500", "12|twelve"),
    )
    A["board"] = await ask(
        p,
        nw,
        c,
        "What did we tell the board about opening a Berlin office?",
        S,
        "Q22 PPTX",
        need=("Q2 2027", "18"),
    )


async def attachments(U):
    S = "attachments"
    lena, nw = U["lena"], STATE["nw"]
    conv = await conversation(lena, nw)
    quote = FIX / "attach/acme-cloud-backup-quote.docx"
    r = expect(
        S,
        "upload attachment",
        await lena.post(
            f"/orgs/{nw}/attachments",
            files={"file": (quote.name, quote.read_bytes())},
            data={"conversation_id": conv},
        ),
        201,
    )
    att = r.json()
    full = expect(S, "GET attachment", await lena.get(f"/orgs/{nw}/attachments/{att['id']}")).json()
    check(S, "attachment parsed", "SOC 2 Type I" in full["content"], full["content"][:200])
    expect(
        S,
        "someone else can't read it",
        await U["tom"].get(f"/orgs/{nw}/attachments/{att['id']}"),
        404,
    )
    msg = await ask(
        lena,
        nw,
        conv,
        "Does this quote meet our procurement requirements for new vendors? List everything that doesn't comply and who needs to sign off.",
        S,
        "Q10 attachment vs policy",
        need=("Type II", "120", "90", "Security Lead|Ines"),
        attachment_ids=[att["id"]],
    )
    check(
        S,
        "Q10 cites the attachment and the policy",
        {"attachment", "knowledge"} <= {s["origin"] for s in msg.get("sources", [])}
        if msg
        else False,
        msg.get("sources") if msg else None,
    )
    r = expect(
        S,
        "conversation attachments",
        await lena.get(f"/orgs/{nw}/conversations/{conv}/attachments"),
    )
    check(S, "one attachment", len(r.json()) == 1, r.json())
    r = expect(
        S,
        "spare attachment",
        await lena.post(f"/orgs/{nw}/attachments", files={"file": ("note.txt", b"scratch")}),
        201,
    )
    expect(
        S, "DELETE attachment", await lena.delete(f"/orgs/{nw}/attachments/{r.json()['id']}"), 204
    )
    expect(
        S,
        "attachment in use elsewhere",
        await lena.post(
            f"/orgs/{nw}/conversations/{await conversation(lena, nw)}/messages",
            params={"stream": "false"},
            json={"content": "hi", "attachment_ids": [att["id"]]},
        ),
        400,
    )


async def feedback_verified_sharing(U):
    S = "verify+share"
    p, m, lena, t, o = U["priya"], U["marcus"], U["lena"], U["tom"], U["omar"]
    nw = STATE["nw"]
    A = STATE["answers"]
    expect(
        S,
        "upvote",
        await p.post(f"/orgs/{nw}/messages/{A['soc2']['id']}/feedback", json={"feedback": "up"}),
        204,
    )
    expect(
        S,
        "Tom downvotes",
        await t.post(
            f"/orgs/{nw}/messages/{A['tom_vendors']['id']}/feedback", json={"feedback": "down"}
        ),
        204,
    )
    expect(
        S,
        "can't vote on someone else's answer",
        await t.post(f"/orgs/{nw}/messages/{A['soc2']['id']}/feedback", json={"feedback": "down"}),
        404,
    )
    doc = STATE["doc"]("employee handbook")
    r = expect(
        S,
        "document feedback",
        await t.post(
            f"/orgs/{nw}/documents/{doc['id']}/feedback",
            json={"vote": "down", "message_id": A["pto"]["id"]},
        ),
    )
    check(S, "vote counted", r.json()["feedback_down"] == 1, r.json())
    expect(
        S,
        "clear document vote",
        await t.post(f"/orgs/{nw}/documents/{doc['id']}/feedback", json={"vote": None}),
    )

    # Verified answer from the sponsorship answer, in the org-wide handbook.
    r = expect(
        S,
        "verify an answer",
        await p.post(
            f"/orgs/{nw}/verified-answers",
            json={
                "message_id": A["sponsor"]["id"],
                "collection_id": STATE["col"]["handbook"],
                "owner_id": U["marcus"].id,
                "expires_in_days": 60,
            },
        ),
        201,
    )
    va = r.json()
    check(
        S,
        "verified answer fields",
        va["status"] == "active"
        and va["owner"]["id"] == U["marcus"].id
        and va["question"].startswith("I want to sponsor"),
        va,
    )
    r = expect(
        S,
        "verify into a restricted collection widens access?",
        await p.post(
            f"/orgs/{nw}/verified-answers",
            json={"message_id": A["soc2"]["id"], "collection_id": STATE["col"]["handbook"]},
        ),
        400,
    )
    check(
        S, "refused: sources_not_visible", r.json().get("code") == "sources_not_visible", r.json()
    )
    expect(
        S,
        "member can't verify",
        await lena.post(
            f"/orgs/{nw}/verified-answers",
            json={"question": "q", "answer": "a", "collection_id": STATE["col"]["handbook"]},
        ),
        403,
    )
    expect(S, "list verified", await t.get(f"/orgs/{nw}/verified-answers"))
    expect(S, "GET verified", await t.get(f"/orgs/{nw}/verified-answers/{va['id']}"))
    expect(
        S,
        "PATCH verified",
        await p.patch(
            f"/orgs/{nw}/verified-answers/{va['id']}",
            json={"question": "Who approves a $12,000 conference sponsorship?"},
        ),
    )
    r = expect(
        S,
        "reverify",
        await m.post(
            f"/orgs/{nw}/verified-answers/{va['id']}/reverify", json={"expires_in_days": 120}
        ),
    )
    check(
        S,
        "Marcus is the verifier now",
        r.json()["verified_by"]["id"] == U["marcus"].id,
        r.json()["verified_by"],
    )
    r = await p.post(
        f"/orgs/{nw}/verified-answers",
        json={
            "question": "Temporary",
            "answer": "Temporary answer.",
            "collection_id": STATE["col"]["scratch"],
        },
    )
    expect(S, "written verified answer", r, 201)
    expect(
        S, "DELETE verified", await p.delete(f"/orgs/{nw}/verified-answers/{r.json()['id']}"), 204
    )

    async def indexed():
        res = (
            await t.post(
                f"/orgs/{nw}/search",
                json={"query": "Who approves a $12,000 conference sponsorship?"},
            )
        ).json()["results"]
        return res if any(x["source_kind"] == "verified" for x in res) else None

    await wait_until("verified answer indexed", indexed, limit=300)
    ct = await conversation(t, nw)
    msg = await ask(
        t,
        nw,
        ct,
        "Who approves a $12,000 conference sponsorship?",
        S,
        "Q19 verified answer",
        need=("Amara Okafor",),
    )
    check(
        S,
        "Q19 cites the verified answer",
        any(s.get("source_kind") == "verified" for s in msg.get("sources", [])),
        msg.get("sources"),
    )

    # Sharing: an answer built from the restricted Vendors collection.
    r = expect(S, "share", await p.post(f"/orgs/{nw}/messages/{A['soc2']['id']}/share"))
    token = r.json()["token"]
    expect(
        S,
        "can't share someone else's answer",
        await t.post(f"/orgs/{nw}/messages/{A['soc2']['id']}/share"),
        404,
    )
    full = expect(S, "Lena opens it", await lena.get(f"/shared/{token}")).json()
    check(
        S,
        "Lena: full",
        full["access"] == "full" and full["answer"] and full["sources"],
        full["access"],
    )
    cited = set(int(n) for n in re.findall(r"\[(\d+)\]", A["soc2"]["content"]))
    check(
        S,
        "shared sources are only the cited ones",
        {s["ref"] for s in full["sources"]} <= cited,
        ({s["ref"] for s in full["sources"]}, cited),
    )
    lim = expect(S, "Tom opens it", await t.get(f"/shared/{token}")).json()
    check(
        S,
        "Tom: restricted, told what's missing",
        lim["access"] == "restricted"
        and lim["answer"] is None
        and any(x["collection_name"] == "Vendors & Contracts" for x in lim["missing"]),
        lim,
    )
    none = expect(S, "Omar opens it", await o.get(f"/shared/{token}")).json()
    check(
        S, "Omar (not a member): none", none["access"] == "none" and none["question"] is None, none
    )
    vendors_id = next(x["collection_id"] for x in lim["missing"] if x["collection_id"])
    r = expect(
        S,
        "Tom requests access",
        await t.post(
            f"/orgs/{nw}/collections/{vendors_id}/access-requests",
            json={"note": "Writing the vendor onboarding guide"},
        ),
        201,
    )
    tom_req = r.json()["id"]
    r = expect(
        S,
        "Lena requests Leadership",
        await lena.post(
            f"/orgs/{nw}/collections/{STATE['col']['leadership']}/access-requests",
            json={"note": "Preparing Berlin hiring plan"},
        ),
        201,
    )
    lena_req = r.json()["id"]
    expect(
        S,
        "already has access",
        await lena.post(f"/orgs/{nw}/collections/{vendors_id}/access-requests", json={}),
        409,
    )
    reqs = expect(S, "list access requests", await p.get(f"/orgs/{nw}/access-requests")).json()
    check(S, "two pending", len(reqs) == 2, reqs)
    expect(S, "dismiss Tom's", await p.post(f"/orgs/{nw}/access-requests/{tom_req}/dismiss"))
    expect(S, "approve Lena's", await p.post(f"/orgs/{nw}/access-requests/{lena_req}/approve"))
    expect(S, "decided twice", await p.post(f"/orgs/{nw}/access-requests/{lena_req}/approve"), 409)
    cl = await conversation(lena, nw)
    await ask(
        lena,
        nw,
        cl,
        "What is the salary range for an L5 staff engineer?",
        S,
        "Q12b after access granted",
        need=("195,000", "240,000"),
    )
    expect(S, "unshare", await p.delete(f"/orgs/{nw}/messages/{A['soc2']['id']}/share"), 204)
    expect(S, "link is dead", await lena.get(f"/shared/{token}"), 404)


async def gaps_and_requests(U):
    S = "gaps"
    p, lena, t = U["priya"], U["lena"], U["tom"]
    nw = STATE["nw"]
    A = STATE["answers"]
    r = expect(
        S,
        "request from the gap answer",
        await lena.post(
            f"/orgs/{nw}/doc-requests",
            json={
                "title": "Germany parental leave policy",
                "note": "I'm based in Berlin",
                "message_id": A["germany"]["id"],
            },
        ),
        201,
    )
    req = r.json()
    check(S, "question taken from the chat", req["question"] and "Germany" in req["question"], req)
    r = expect(
        S,
        "same title again",
        await lena.post(
            f"/orgs/{nw}/doc-requests", json={"title": "germany parental leave policy."}
        ),
        200,
    )
    check(S, "deduplicated", r.json()["id"] == req["id"], r.json()["id"])
    r = expect(
        S,
        "Tom requests",
        await t.post(
            f"/orgs/{nw}/doc-requests",
            json={
                "title": "Kubernetes migration plan",
                "question": "When are we migrating to Kubernetes?",
            },
        ),
        201,
    )
    tom_req = r.json()["id"]
    expect(S, "member can't list all", await lena.get(f"/orgs/{nw}/doc-requests"), 403)
    r = expect(S, "admin list", await p.get(f"/orgs/{nw}/doc-requests")).json()
    check(S, "2 pending", r["total"] == 2, r)
    expect(S, "mine", await lena.get(f"/orgs/{nw}/doc-requests/mine"))
    expect(
        S,
        "dismiss Tom's",
        await p.post(
            f"/orgs/{nw}/doc-requests/{tom_req}/dismiss", json={"note": "We're staying on ECS"}
        ),
    )
    gaps = expect(S, "gaps", await p.get(f"/orgs/{nw}/gaps")).json()
    print(
        "   gaps:",
        json.dumps(
            [
                {k: c[k] for k in ("label", "count", "reasons", "addressed", "missing")}
                for c in gaps["clusters"]
            ],
            indent=None,
        )[:2000],
        flush=True,
    )
    labels = " | ".join(c["label"] for c in gaps["clusters"])
    check(S, "Germany question is a gap", "germany" in labels.lower(), labels)
    check(
        S,
        "Tom's downvote is a gap",
        any(c["reasons"].get("downvoted") for c in gaps["clusters"]),
        labels,
    )
    renewals = [c for c in gaps["clusters"] if "auto-renewing" in c["label"]]
    claimed = [
        m
        for c in renewals
        for m in c["missing"]
        if re.search(r"cut off|truncated|remaining rows", m, re.I)
    ]
    check(S, "records results count as evidence (no register 'cut off')", not claimed, claimed)
    germany = next((c for c in gaps["clusters"] if "germany" in c["label"].lower()), None)
    check(
        S,
        "request attached to the cluster",
        germany and any(x["id"] == req["id"] for x in germany["requests"]),
        germany,
    )
    expect(S, "member can't see gaps", await lena.get(f"/orgs/{nw}/gaps"), 403)
    # Fill it: upload the addendum, fulfil, re-ask.
    addendum = FIX / "northwind/later/germany-parental-leave-addendum.md"
    expect(S, "upload addendum", await upload(p, nw, STATE["src"]["handbook"], [addendum]))
    await source_ready(p, nw, STATE["src"]["handbook"], 7)
    docs = (await p.get(f"/orgs/{nw}/documents", params={"q": "Germany"})).json()["items"]
    check(S, "addendum indexed", len(docs) == 1, docs)
    if docs:
        r = expect(
            S,
            "fulfil",
            await p.post(
                f"/orgs/{nw}/doc-requests/{req['id']}/fulfill",
                json={"document_id": docs[0]["id"], "note": "Added the Germany addendum"},
            ),
        )
        check(
            S,
            "fulfilled with the document",
            r.json()["status"] == "fulfilled" and r.json()["document"]["id"] == docs[0]["id"],
            r.json(),
        )
        expect(
            S,
            "fulfil twice",
            await p.post(
                f"/orgs/{nw}/doc-requests/{req['id']}/fulfill", json={"document_id": docs[0]["id"]}
            ),
            409,
        )
    mine = expect(
        S, "Lena sees it fulfilled", await lena.get(f"/orgs/{nw}/doc-requests/mine")
    ).json()
    check(
        S,
        "fulfilled in mine",
        mine["items"][0]["status"] == "fulfilled",
        mine["items"][0]["status"],
    )
    cl = await conversation(lena, nw)
    await ask(
        lena,
        nw,
        cl,
        "What is the parental leave policy for employees in our Germany office?",
        S,
        "Q18 re-ask after fulfil",
        need=("14 weeks|fourteen weeks", "Elterngeld"),
    )
    gaps = expect(S, "gaps after", await p.get(f"/orgs/{nw}/gaps")).json()
    germany = next((c for c in gaps["clusters"] if "germany" in c["label"].lower()), None)
    check(S, "cluster marked addressed", germany and germany["addressed"], germany)


async def agents(U):
    S = "agents"
    p, lena, nw = U["priya"], U["lena"], STATE["nw"]
    body = {
        "name": "Procurement assistant",
        "description": "Vendor and spend questions",
        "mode": "quick",
        "glyph": "chart",
        "instructions": "You help with vendor contracts and purchasing. Always name the approver and the deadline when relevant.",
        "collection_ids": [STATE["col"]["vendors"], STATE["col"]["handbook"]],
        "starter_prompts": ["Which contracts renew next quarter?"],
    }
    r = expect(S, "create agent", await p.post(f"/orgs/{nw}/agents", json=body), (200, 201))
    agent = r.json()
    expect(S, "member can't create", await lena.post(f"/orgs/{nw}/agents", json=body), 403)
    expect(
        S,
        "PATCH agent",
        await p.patch(
            f"/orgs/{nw}/agents/{agent['id']}",
            json={
                "instructions": body["instructions"] + " Quote contract clauses exactly.",
                "starter_prompts": [
                    "Which contracts renew next quarter?",
                    "Who approves a $40k purchase?",
                ],
            },
        ),
    )
    r = expect(S, "list agents", await lena.get(f"/orgs/{nw}/agents"))
    check(S, "agent listed", any(a["id"] == agent["id"] for a in r.json()), r.json())
    conv = await conversation(lena, nw, agent_id=agent["id"])
    msg = await ask(
        lena,
        nw,
        conv,
        "Who has to approve a $40,000 purchase, and does our Datadog contract cap renewal price increases?",
        S,
        "Q17 agent",
        need=("Daniel Reyes|CFO|Chief Financial Officer", "5%|five percent"),
    )
    allowed = {"Vendors & Contracts", "Company Handbook"}
    check(
        S,
        "agent stays in its collections",
        {s.get("collection_name") for s in msg.get("sources", [])} <= allowed,
        {s.get("collection_name") for s in msg.get("sources", [])},
    )
    r = expect(
        S,
        "scratch agent",
        await p.post(f"/orgs/{nw}/agents", json={**body, "name": "Scratch agent"}),
        (200, 201),
    )
    expect(S, "DELETE agent", await p.delete(f"/orgs/{nw}/agents/{r.json()['id']}"), 204)


async def cross_org_controls(U):
    S = "cross-org"
    p, o = U["priya"], U["omar"]
    nw, hx = STATE["nw"], STATE["hx"]
    expect(
        S,
        "Helix turns off cross-org",
        await o.patch(f"/orgs/{hx}", json={"settings": {"allow_cross_org": False}}),
    )
    r = expect(
        S,
        "search after opt-out",
        await p.post(
            f"/orgs/{nw}/search",
            json={"query": "lost device reporting deadline", "org_slugs": [nw, hx]},
        ),
    ).json()
    check(
        S,
        "Helix excluded",
        hx not in {x["org_slug"] for x in r["results"]},
        {x["org_slug"] for x in r["results"]},
    )
    r = expect(
        S,
        "from Helix's own workspace",
        await p.post(
            f"/orgs/{hx}/search",
            json={"query": "lost device reporting deadline", "org_slugs": [hx, nw]},
        ),
    ).json()
    check(
        S,
        "primary org always searched",
        hx in {x["org_slug"] for x in r["results"]},
        {x["org_slug"] for x in r["results"]},
    )
    expect(
        S,
        "Helix turns it back on",
        await o.patch(f"/orgs/{hx}", json={"settings": {"allow_cross_org": True}}),
    )
    expect(
        S, "Priya can't admin Helix", await p.patch(f"/orgs/{hx}", json={"name": "Mine now"}), 403
    )


async def keys_and_mcp(U):
    S = "api keys + MCP"
    p, nw = U["priya"], STATE["nw"]
    r = expect(
        S,
        "create member key",
        await p.post(
            f"/orgs/{nw}/api-keys",
            json={"name": "Ops bot", "role": "member", "expires_in_days": 30},
        ),
        (200, 201),
    )
    member_key = r.json()
    check(S, "key shown once", member_key["key"].startswith("strata_sk_"), member_key.keys())
    r = expect(
        S,
        "create admin key",
        await p.post(f"/orgs/{nw}/api-keys", json={"name": "SIEM export", "role": "admin"}),
        (200, 201),
    )
    admin_key = r.json()
    keys = expect(S, "list keys", await p.get(f"/orgs/{nw}/api-keys")).json()
    check(S, "no secrets listed", all("key" not in k for k in keys) and len(keys) == 2, keys)
    expect(
        S,
        "member can't create keys",
        await U["lena"].post(f"/orgs/{nw}/api-keys", json={"name": "x", "role": "member"}),
        403,
    )
    bot = client(member_key["key"])
    r = expect(
        S,
        "REST search with key",
        await bot.post(f"/api/orgs/{nw}/search", json={"query": "per diem international travel"}),
    )
    check(
        S,
        "key search finds the policy",
        any("travel" in x["title"].lower() for x in r.json()["results"]),
        [x["title"] for x in r.json()["results"]],
    )
    r = expect(S, "member key can't see Leadership", await bot.get(f"/api/orgs/{nw}/collections"))
    check(
        S,
        "key collections",
        "Leadership" not in [c["name"] for c in r.json()]
        and "Vendors & Contracts" not in [c["name"] for c in r.json()],
        [c["name"] for c in r.json()],
    )
    expect(
        S,
        "member key can't create collections",
        await bot.post(f"/api/orgs/{nw}/collections", json={"name": "Bot"}),
        403,
    )
    expect(
        S,
        "key can't create keys (session only)",
        await client(admin_key["key"]).post(
            f"/api/orgs/{nw}/api-keys", json={"name": "x", "role": "member"}
        ),
        (401, 403),
    )
    conv = (await bot.post(f"/api/orgs/{nw}/conversations", json={})).json()["id"]
    r = expect(
        S,
        "chat with key",
        await bot.post(
            f"/api/orgs/{nw}/conversations/{conv}/messages",
            params={"stream": "false"},
            json={"content": "What is the per diem for international travel?"},
        ),
    )
    check(
        S,
        "key chat answer",
        "110" in r.json()["message"]["content"],
        r.json()["message"]["content"][:300],
    )

    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    @contextlib.asynccontextmanager
    async def session(key):
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=600)
        async with (
            http,
            streamable_http_client(f"{BASE}/api/mcp", http_client=http) as (read, write),
            ClientSession(read, write) as s,
        ):
            await s.initialize()
            yield s

    def data(res):
        if res.is_error:
            raise AssertionError(res.content[0].text)
        return res.structured_content or json.loads(res.content[0].text)

    MS = "mcp"
    async with session(admin_key["key"]) as s:
        tools = {t.name for t in (await s.list_tools()).tools}
        check(
            MS,
            "8 tools",
            tools
            == {
                "search",
                "read_document",
                "list_collections",
                "ask",
                "list_record_types",
                "query_records",
                "resolve_role",
                "scan",
            },
            tools,
        )
        cols = data(await s.call_tool("list_collections", {}))
        check(
            MS,
            "admin key sees all collections",
            {"Leadership", "Vendors & Contracts"} <= {c["name"] for c in cols["collections"]},
            cols,
        )
        types = data(await s.call_tool("list_record_types", {}))
        names = {t["name"] for t in types["record_types"]}
        check(MS, "record types", "contract_terms" in names and len(names) == 4, names)
        reg = STATE["schemas"]["register"]["name"]
        res = data(await s.call_tool("query_records", {"record_type": reg, "mode": "count"}))
        check(MS, "query_records count 30", res.get("total") == 30, str(res)[:300])
        res = data(await s.call_tool("resolve_role", {"role": "Head of People"}))
        check(MS, "resolve_role Rachel Moore", "Rachel Moore" in json.dumps(res), str(res)[:300])
        res = data(await s.call_tool("search", {"query": "SAML certificate rotation outage"}))
        check(
            MS,
            "search finds the SSO postmortem",
            any(
                "sign-on" in r["title"].lower() or "sso" in r["title"].lower()
                for r in res["results"]
            ),
            [r["title"] for r in res["results"]],
        )
        doc_id = res["results"][0]["document_id"]
        page = data(await s.call_tool("read_document", {"document_id": doc_id}))
        check(MS, "read_document", "SAML" in page["content"], page["content"][:200])
        res = data(
            await s.call_tool(
                "scan",
                {
                    "question": "Was this incident caused by a configuration change? Answer with the change.",
                    "title_contains": "Postmortem",
                },
            )
        )
        hits = sorted(m["title"] for m in res["matches"])
        print(
            "   scan config-change matches:",
            hits,
            res["documents_matching_filter"],
            res["checked"],
            res["complete"],
            res["cost_usd"],
            flush=True,
        )
        check(
            MS,
            "scan checked all 14",
            res["documents_matching_filter"] == 14 and res["checked"] == 14 and res["complete"],
            res,
        )
        want = {
            "Telemetry ingestion lag",
            "Single sign-on outage",
            "EU region DNS outage",
            "Gaps in battery telemetry",
            "Fleet stopped at a customer warehouse",
            "API rate limiter misfire",
        }
        got = {t.replace("Postmortem: ", "") for t in hits}
        check(MS, "scan found the config-change incidents", want <= got, (want - got, got - want))
        res = data(
            await s.call_tool("ask", {"question": "What is the hotel limit per night in London?"})
        )
        check(
            MS,
            "ask answers with sources",
            "300" in res["answer"] and res["sources"],
            res["answer"][:300],
        )
    async with session(member_key["key"]) as s:
        cols = data(await s.call_tool("list_collections", {}))
        check(
            MS,
            "member key sees no restricted collections",
            not {"Leadership", "Vendors & Contracts"} & {c["name"] for c in cols["collections"]},
            cols,
        )
        denied = await s.call_tool(
            "query_records", {"record_type": STATE["schemas"]["register"]["name"]}
        )
        check(MS, "member key can't query the vendor register", denied.is_error, denied.content)
    expect(S, "revoke member key", await p.delete(f"/orgs/{nw}/api-keys/{member_key['id']}"), 204)
    expect(
        S, "revoked key refused", await bot.post(f"/api/orgs/{nw}/search", json={"query": "x"}), 401
    )
    hdrs = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {member_key['key']}",
    }
    r = await httpx.AsyncClient().post(
        f"{BASE}/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=hdrs
    )
    check(
        MS,
        "revoked key refused by MCP",
        r.status_code == 401 and "Bearer" in r.headers.get("www-authenticate", ""),
        r.status_code,
    )
    STATE["admin_key"] = admin_key


async def conversations_admin(U):
    S = "conversations"
    p, nw = U["priya"], STATE["nw"]
    page = expect(S, "list conversations", await p.get(f"/orgs/{nw}/conversations")).json()
    check(S, "Priya's conversations listed", page["total"] >= 3, page["total"])
    conv = page["items"][0]
    detail = expect(
        S, "GET conversation", await p.get(f"/orgs/{nw}/conversations/{conv['id']}")
    ).json()
    check(
        S,
        "messages + titles",
        detail["messages"] and detail["conversation"]["title"],
        detail["conversation"],
    )
    expect(
        S,
        "rename",
        await p.patch(f"/orgs/{nw}/conversations/{conv['id']}", json={"title": "Vendor review"}),
    )
    expect(
        S,
        "Tom can't read Priya's",
        await U["tom"].get(f"/orgs/{nw}/conversations/{conv['id']}"),
        404,
    )
    # Cancel a long deep answer mid-stream.
    c = await conversation(p, nw, mode="deep")
    message_id = None
    async with p.http.stream(
        "POST",
        f"/api/orgs/{nw}/conversations/{c}/messages",
        json={
            "content": "Compare every postmortem's action items and tell me which teams own the most follow-up work, with reasons."
        },
    ) as r:
        event = None
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event == "start":
                message_id = json.loads(line[6:])["assistant_message_id"]
            elif line.startswith("data: ") and event == "step":
                break
    r = expect(S, "cancel", await p.post(f"/orgs/{nw}/messages/{message_id}/cancel"))
    check(S, "stopped", r.json()["status"] == "stopped", r.json()["status"])
    r = expect(
        S, "cancel again (idempotent)", await p.post(f"/orgs/{nw}/messages/{message_id}/cancel")
    )
    expect(S, "DELETE conversation", await p.delete(f"/orgs/{nw}/conversations/{c}"), 204)


async def documents_admin(U):
    S = "documents admin"
    p, t, nw = U["priya"], U["tom"], STATE["nw"]
    doc = STATE["doc"]("onboarding")
    r = expect(S, "boost", await p.patch(f"/orgs/{nw}/documents/{doc['id']}", json={"boost": 2}))
    check(S, "boost saved", r.json()["boost"] == 2, r.json()["boost"])
    expect(
        S,
        "member can't boost",
        await U["lena"].patch(f"/orgs/{nw}/documents/{doc['id']}", json={"boost": 1}),
        403,
    )
    expect(S, "hide", await p.patch(f"/orgs/{nw}/documents/{doc['id']}", json={"hidden": True}))
    r = (
        await t.post(
            f"/orgs/{nw}/search", json={"query": "new hire onboarding checklist FileVault day 1"}
        )
    ).json()
    check(
        S,
        "hidden doc out of search",
        all(x["document_id"] != doc["id"] for x in r["results"]),
        [x["title"] for x in r["results"]],
    )
    r = (await t.get(f"/orgs/{nw}/documents", params={"hidden": "true"})).json()
    expect(
        S,
        "unhide",
        await p.patch(f"/orgs/{nw}/documents/{doc['id']}", json={"hidden": False, "boost": 0}),
    )
    # A hidden document's records disappear from queries.
    incident = STATE["doc"]("warehouse")
    if incident and STATE.get("schemas"):
        name = STATE["schemas"]["incidents"]["name"]
        before = (
            await p.post(f"/orgs/{nw}/records/query", json={"schema": name, "mode": "count"})
        ).json()["total"]
        await p.patch(f"/orgs/{nw}/documents/{incident['id']}", json={"hidden": True})
        after = (
            await p.post(f"/orgs/{nw}/records/query", json={"schema": name, "mode": "count"})
        ).json()["total"]
        check(S, "hidden doc's records excluded", after == before - 1, (before, after))
        await p.patch(f"/orgs/{nw}/documents/{incident['id']}", json={"hidden": False})
    r = expect(
        S, "upload an obsolete file", await upload(p, nw, STATE["src"]["handbook"], []), (400, 422)
    )
    old = await p.post(
        f"/orgs/{nw}/sources/{STATE['src']['handbook']}/files",
        files=[
            ("files", ("old-expense-policy-2019.txt", b"Old 2019 expense policy: per diem $40."))
        ],
    )
    expect(S, "upload obsolete doc", old)
    await source_ready(p, nw, STATE["src"]["handbook"], 8)
    d = (await p.get(f"/orgs/{nw}/documents", params={"q": "2019"})).json()["items"]
    if check(S, "obsolete doc indexed", len(d) == 1, d):
        expect(S, "DELETE document", await p.delete(f"/orgs/{nw}/documents/{d[0]['id']}"), 204)
    expect(
        S,
        "DELETE scratch collection",
        await p.delete(f"/orgs/{nw}/collections/{STATE['col']['scratch']}"),
        204,
    )


async def insights_audit(U):
    S = "audit"
    p, lena, nw, hx = U["priya"], U["lena"], STATE["nw"], STATE["hx"]
    ins = expect(S, "insights", await p.get(f"/orgs/{nw}/insights", params={"days": 7})).json()
    print("   insights totals:", ins["totals"], "usage:", ins["usage"], flush=True)
    check(
        S,
        "insights counts questions",
        ins["totals"]["questions"] >= 20 and ins["totals"]["active_users"] >= 4,
        ins["totals"],
    )
    check(
        S,
        "insights prices usage",
        ins["usage"] and ins["usage"][0]["cost_usd"] is not None,
        ins["usage"],
    )
    expect(S, "member can't see insights", await lena.get(f"/orgs/{nw}/insights"), 403)
    actions = set()
    cursor = None
    while True:
        page = expect(
            S,
            "events page",
            await p.get(
                f"/orgs/{nw}/audit/events",
                params={"limit": 200, **({"cursor": cursor} if cursor else {})},
            ),
        ).json()
        actions |= {e["action"] for e in page["items"]}
        cursor = page["next_cursor"]
        if not cursor:
            break
    print("   actions:", sorted(actions), flush=True)
    wanted = {
        "org.created",
        "org.updated",
        "member.joined",
        "member.role_changed",
        "member.removed",
        "invite.created",
        "invite.revoked",
        "collection.created",
        "collection.updated",
        "collection.deleted",
        "source.created",
        "source.updated",
        "source.synced",
        "source.files_uploaded",
        "source.deleted",
        "document.hidden",
        "document.unhidden",
        "document.boosted",
        "document.deleted",
        "api_key.created",
        "api_key.revoked",
        "share.created",
        "share.revoked",
        "access_request.approved",
        "access_request.dismissed",
        "verified_answer.created",
        "verified_answer.updated",
        "verified_answer.reverified",
        "verified_answer.deleted",
        "record_schema.suggested",
        "record_schema.created",
        "record_schema.activated",
        "record_schema.updated",
        "record_schema.extracted",
        "record_schema.deleted",
        "doc_request.fulfilled",
        "doc_request.dismissed",
        "agent.created",
        "agent.updated",
        "agent.deleted",
        "graph.rebuilt",
    }
    check(S, "every admin action audited", wanted <= actions, sorted(wanted - actions))
    page = expect(
        S, "access log", await p.get(f"/orgs/{nw}/audit/access", params={"limit": 200})
    ).json()
    kinds = {e["kind"] for e in page["items"]}
    channels = {e["channel"] for e in page["items"]}
    check(S, "access kinds", {"chat", "search", "read", "shared"} <= kinds, kinds)
    check(S, "access channels", {"app", "api", "mcp"} <= channels, channels)
    r = expect(
        S, "filter channel=mcp", await p.get(f"/orgs/{nw}/audit/access", params={"channel": "mcp"})
    ).json()
    check(
        S,
        "mcp entries name the key",
        r["items"]
        and all(
            e["actor"]["api_key_name"] == "SIEM export" or e["actor"]["api_key_name"]
            for e in r["items"]
        ),
        r["items"][:1],
    )
    vendor = STATE["doc"]("vendor register")
    r = expect(
        S,
        "who read the vendor register",
        await p.get(f"/orgs/{nw}/audit/access", params={"document_id": vendor["id"]}),
    ).json()
    readers = {e["actor"]["email"] for e in r["items"]}
    check(
        S,
        "register reached by Priya and Lena, never Tom",
        {U["priya"].email, U["lena"].email} <= readers and U["tom"].email not in readers,
        readers,
    )
    r = expect(
        S,
        "records queries logged",
        await p.get(f"/orgs/{nw}/audit/access", params={"q": "records", "kind": "search"}),
    ).json()
    check(S, "records reads in the log", len(r["items"]) > 0, r["items"][:1])
    r = expect(
        S, "actor filter", await p.get(f"/orgs/{nw}/audit/access", params={"actor_id": U["tom"].id})
    ).json()
    check(
        S,
        "only Tom",
        r["items"] and {e["actor"]["email"] for e in r["items"]} == {U["tom"].email},
        {e["actor"]["email"] for e in r["items"]},
    )
    r = expect(S, "paginate", await p.get(f"/orgs/{nw}/audit/access", params={"limit": 5})).json()
    if check(S, "next cursor", r["next_cursor"], r):
        r2 = expect(
            S,
            "page 2",
            await p.get(
                f"/orgs/{nw}/audit/access", params={"limit": 5, "cursor": r["next_cursor"]}
            ),
        ).json()
        check(
            S, "no overlap", not {e["id"] for e in r["items"]} & {e["id"] for e in r2["items"]}, ""
        )
    expect(
        S, "bad cursor", await p.get(f"/orgs/{nw}/audit/access", params={"cursor": "garbage"}), 400
    )
    expect(
        S,
        "bad range",
        await p.get(
            f"/orgs/{nw}/audit/access",
            params={"since": "2026-12-01T00:00:00", "until": "2026-01-01T00:00:00"},
        ),
        400,
    )
    r = expect(
        S,
        "export csv",
        await p.get(f"/orgs/{nw}/audit/export", params={"log": "access", "format": "csv"}),
    )
    rows = list(csv.DictReader(io.StringIO(r.text)))
    check(
        S,
        "csv rows",
        len(rows) > 20 and "documents" in rows[0],
        (len(rows), list(rows[0])[:12] if rows else None),
    )
    r = expect(
        S,
        "export jsonl",
        await p.get(f"/orgs/{nw}/audit/export", params={"log": "events", "format": "jsonl"}),
    )
    lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    check(S, "jsonl events, oldest first", lines and lines[0]["action"] == "org.created", lines[:1])
    secret_bits = [STATE["admin_key"]["key"], PASSWORD, STATE["admin_key"]["key"][:16]]
    check(S, "no secrets in the event log", not any(s in r.text for s in secret_bits), "")
    expect(S, "member can't read audit", await lena.get(f"/orgs/{nw}/audit/events"), 403)
    expect(S, "non-member gets 404", await U["omar"].get(f"/orgs/{nw}/audit/events"), 404)
    akey = client(STATE["admin_key"]["key"])
    expect(
        S,
        "admin key reads audit (SIEM)",
        await akey.get(f"/api/orgs/{nw}/audit/events", params={"limit": 3}),
    )
    r = expect(
        S, "Helix audit", await U["omar"].get(f"/orgs/{hx}/audit/access", params={"limit": 200})
    ).json()
    cross = [e for e in r["items"] if e["cross_org"]]
    check(
        S,
        "Helix sees Priya's cross-org read",
        cross and cross[0]["actor"]["email"] == U["priya"].email,
        r["items"][:2],
    )
    helix_titles = {"Helix Labs Security Handbook", "Statement of Work: Project Atlas"}
    titles = {d["title"] for e in cross for d in e["documents"]}
    check(
        S,
        "Helix log lists only Helix documents",
        titles and titles <= helix_titles and nw not in json.dumps(cross),
        (titles, json.dumps(cross)[:300]),
    )


async def slack_unconfigured(U):
    S = "slack (unconfigured)"
    p, nw = U["priya"], STATE["nw"]
    r = expect(S, "slack overview", await p.get(f"/orgs/{nw}/slack"))
    check(S, "not configured", r.json()["configured"] is False, r.json())
    expect(
        S,
        "install needs config",
        await p.get(f"/orgs/{nw}/slack/install", follow_redirects=False),
        503,
    )
    expect(
        S, "events need config", await client().post("/api/slack/events", content=b"{}"), (401, 503)
    )
    expect(S, "links (none)", await p.get("/slack/links"))


async def finish(U):
    S = "leave"
    t, nw = U["tom"], STATE["nw"]
    expect(S, "Tom leaves", await t.delete(f"/orgs/{nw}/members/{t.id}"), 204)
    expect(S, "Tom is out", await t.get(f"/orgs/{nw}"), 404)


def coverage():
    spec = httpx.get(f"{BASE}/api/openapi.json").json()
    ops = []
    for path, methods in spec["paths"].items():
        for m in methods:
            if m in ("get", "post", "put", "patch", "delete"):
                rx = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
                ops.append((m.upper(), path, rx, path.count("{")))
    ops.sort(key=lambda x: x[3])
    hit = {}
    for method, path, status in CALLS:
        for m, tpl, rx, _ in ops:
            if m == method and rx.match(path):
                hit.setdefault((m, tpl), set()).add(status)
                break
    missing = [(m, tpl) for m, tpl, _, _ in ops if (m, tpl) not in hit]
    return len(ops), hit, missing


async def main():
    U = {
        k: User(k, n)
        for k, n in [
            ("priya", "Priya Raman"),
            ("marcus", "Marcus Bell"),
            ("lena", "Lena Fischer"),
            ("tom", "Tom Alvarez"),
            ("omar", "Omar Haddad"),
            ("jules", "Jules Martin"),
        ]
    }
    phases = [
        accounts,
        orgs,
        members,
        collections,
        sources,
        documents,
        search,
        records,
        graph,
        chat,
        attachments,
        feedback_verified_sharing,
        gaps_and_requests,
        agents,
        cross_org_controls,
        keys_and_mcp,
        conversations_admin,
        documents_admin,
        insights_audit,
        slack_unconfigured,
        finish,
    ]
    only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else None
    t0 = time.monotonic()
    for phase in phases:
        if only and phase.__name__ not in only:
            continue
        print(f"\n=== {phase.__name__} ({time.monotonic() - t0:.0f}s)", flush=True)
        try:
            await phase(U)
        except Exception as e:
            check(
                phase.__name__,
                "phase crashed",
                False,
                f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}",
            )
            if phase in (accounts, orgs, members, collections, sources):
                break
        (OUT / "state.json").write_text(
            json.dumps({k: v for k, v in STATE.items() if k not in ("doc",)}, default=str, indent=1)
        )
    total, hit, missing = coverage()
    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=1))
    (OUT / "answers.json").write_text(json.dumps(ANSWERS, indent=1))
    (OUT / "users.json").write_text(
        json.dumps(
            {k: {"email": u.email, "id": u.id} for k, u in U.items()} | {"password": PASSWORD},
            indent=1,
        )
    )
    fails = [r for r in RESULTS if not r["ok"]]
    print(
        f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed in {time.monotonic() - t0:.0f}s; run {RUN}"
    )
    print(f"endpoint coverage: {len(hit)}/{total}; not hit: {missing}")
    for f in fails:
        print("FAIL", f["section"], "|", f["name"], "|", f["detail"][:400])


asyncio.run(main())
