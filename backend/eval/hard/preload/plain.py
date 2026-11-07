"""Plain-first, the ablation for terms.py: the same injection point and wording, filled from
a search of the question instead of the graph. If it matches terms-first, the gain comes
from handing the model context up front, not from the graph.

20 lines because terms-first injected a median of 20 (at most 61). Each keeps up to 200
characters, the cap terms.py puts on a relation's description, plus the updated date a
normal search result shows.
"""

import re
import time

from app.agent import engine as E
from app.core.db import SessionLocal
from app.retrieval.hybrid import hybrid_search

LINES, CHARS = 20, 200
SPACE = re.compile(r"\s+")


async def passages(db, collection_ids, question, ranking):
    return await hybrid_search(db, [question], collection_ids, limit=LINES, ranking=ranking)


async def context(tb, db, question):
    new, lines = [], []
    for hit in await passages(db, tb.scope.collection_ids, question, tb.ranking):
        if hit.collection_id not in tb.scope.collections:
            continue
        entry, is_new = tb.registry.register(
            hit, tb.scope.orgs[hit.org_id], tb.scope.collections[hit.collection_id]
        )
        if is_new:
            new.append(entry)
        updated = f" (updated {hit.updated_at.date().isoformat()})" if hit.updated_at else ""
        body = SPACE.sub(" ", hit.content).strip()[:CHARS]
        lines.append(f"- {hit.title}{updated} [{entry.ref}]: {body}")
    return ("\n".join(lines) if lines else None), new


def apply():
    cls = next(c for c in vars(E).values() if isinstance(c, type) and hasattr(c, "_claude_loop"))
    original = cls._claude_loop

    async def wrapped(self, model, system, history, user_content, definitions, deep, seed=None):
        step = await self.em.step_start("search", "Pre-searching")
        started = time.perf_counter()
        async with SessionLocal() as db:
            self.tools.db = db
            ctx, new = await context(self.tools, db, self.req.question)
            self.tools.db = None
        ms = int((time.perf_counter() - started) * 1000)
        await self.em.sources([s.to_source_ref() for s in new])
        await self.em.step_update(
            step,
            label=f"Pre-searched ({ms} ms, {len(ctx.splitlines()) if ctx else 0} lines)",
            status="done",
            queries=[],
            refs=[],
        )
        if ctx:
            user_content = (
                f"{user_content}\n\n<search_context>\nPassages found by searching the "
                "question, retrieved automatically. Use them as leads: follow the chain, cite "
                f"[n], and check anything decisive with search or records.\n{ctx}\n"
                "</search_context>"
            )
        return await original(
            self, model, system, history, user_content, definitions, deep, seed=seed
        )

    cls._claude_loop = wrapped
