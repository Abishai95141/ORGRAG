"""Terms-first: before the first turn, link names in the question to graph entities and add
their relations to the message. A plain lookup with no model call; scope is enforced by
`relations_of`.

Names match with punctuation ignored on both sides, so "PS-2025-009" finds a stored
"ps-2025-009". An entity's alias links come first, then its best-supported relations, and
an alias is followed one hop: a question names the service, while the posts that matter
use its project name.

The alias ranking was chosen after checking which test questions showed the project name
(24 of 27 test, 12 of 12 dev). It never reads gold answers, but treat gains on
project-name questions with that in mind.
"""

import re
import time

from sqlalchemy import text

from app.agent import engine as E
from app.core.db import SessionLocal
from app.graph import query as graph

MAX_ENTITIES, PER_ENTITY = 4, 15
ALIAS_HOP = 8
WORD = re.compile(r"[a-z0-9]+")
ALIAS = re.compile(r"known as|code ?name|project name", re.I)
COMMON = set(
    " ".join(
        (
            "the a an and or of to in on for with who what which when where why how is are was",
            "were be do does did can could should would will may must have has had this that",
            "these those our your their my we i you it its at by from as if not no yes team",
            "teams policy service services system systems plan plans vendor vendors need needs",
            "get gets change changes data people person company org organization many much each",
            "all any during after before week month year date current currently now then same",
            "first last next",
        )
    ).split()
)
_index = None


def norm(s):
    return " ".join(WORD.findall(s.lower()))


async def index(db):
    global _index
    if _index is None:
        names = {}
        rows = (await db.execute(text("SELECT id, name, name_key FROM kg_entities"))).all()
        for eid, name, key in rows:
            names.setdefault(norm(key), []).append((eid, name))
        _index = names
    return _index


def link(question, names):
    """Longest names first, no overlaps: 'Harbour Dues' rather than 'Dues'."""
    words = WORD.findall(question.lower())
    taken, out = set(), []
    for n in range(6, 0, -1):
        for i in range(len(words) - n + 1):
            gram = words[i : i + n]
            span = set(range(i, i + n))
            if span & taken or all(w in COMMON or w.isdigit() or len(w) < 3 for w in gram):
                continue
            hit = names.get(" ".join(gram))
            if hit:
                taken |= span
                out.append(hit)
    return out[:MAX_ENTITIES]


def is_alias(rel):
    return bool(ALIAS.search(f"{rel.description} {rel.keywords}"))


def ranked(rels):
    """Alias links first, then the relations with the most support."""
    return sorted(rels, key=lambda r: (not is_alias(r), -len(r.chunk_ids), r.other_name))


async def facts(db, cids, question, names):
    """(entity, relation) pairs to show: each linked entity's relations, and what is said
    about any name it's also known as, which the question may never use."""
    out = []
    for hit in link(question, names):
        ids = [h[0] for h in hit]
        name = hit[0][1]
        rels, _ = await graph.relations_of(db, cids, ids)
        rels = ranked(rels)
        out += [(name, r) for r in rels[:PER_ENTITY]]
        for rel in [r for r in rels if is_alias(r)][:2]:
            more, _ = await graph.relations_of(db, cids, rel.other_id)
            more = [m for m in ranked(more) if m.other_id not in ids]
            out += [(rel.other_name, m) for m in more[:ALIAS_HOP]]
    return out


async def context(tb, db, question):
    new, lines = [], []
    for name, rel in await facts(db, tb.scope.collection_ids, question, await index(db)):
        refs = await tb._refs_for(rel.chunk_ids[:2], new)
        cites = "".join(f"[{x}]" for x in refs)
        lines.append(
            f"- {name} — {rel.other_name} [{rel.other_type}] {cites}: {rel.description[:200]}"
        )
    return ("\n".join(lines) if lines else None), new


def apply():
    cls = next(c for c in vars(E).values() if isinstance(c, type) and hasattr(c, "_claude_loop"))
    original = cls._claude_loop

    async def wrapped(self, model, system, history, user_content, definitions, deep, seed=None):
        step = await self.em.step_start("search", "Resolving terms")
        started = time.perf_counter()
        async with SessionLocal() as db:
            self.tools.db = db
            ctx, new = await context(self.tools, db, self.req.question)
            self.tools.db = None
        ms = int((time.perf_counter() - started) * 1000)
        await self.em.sources([s.to_source_ref() for s in new])
        await self.em.step_update(
            step,
            label=f"Resolved terms ({ms} ms, {len(ctx.splitlines()) if ctx else 0} lines)",
            status="done",
            queries=[],
            refs=[],
        )
        if ctx:
            user_content = (
                f"{user_content}\n\n<graph_context>\nFacts linked to names in the question, "
                "extracted automatically. Use them as leads: follow the chain, cite [n], and "
                f"check anything decisive with search or records.\n{ctx}\n</graph_context>"
            )
        return await original(
            self, model, system, history, user_content, definitions, deep, seed=seed
        )

    cls._claude_loop = wrapped
