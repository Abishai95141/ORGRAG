"""Sufficient-context check (arXiv 2411.06037): after a research round, a fast model
says whether the sources found so far state everything the question needs, and names
what's missing. The note goes to the answering model before it writes, so it searches
for the missing piece or says plainly that it isn't there, instead of answering from
half the evidence. The eval's main failure was exactly that: multi-hop answers that
found the first fact and stopped.

It never blocks an answer: any error means "no note"."""

import logging
from typing import Any

from app.core.config import get_settings
from app.llm.client import structured_call

log = logging.getLogger(__name__)

MAX_EVIDENCE_CHARS = 24_000
MAX_MISSING = 3
# Results that are complete in themselves (every record matching, every entity, every
# document a scan checked): always shown to the check, whole.
EXACT_TOOLS = frozenset(
    {"query_records", "resolve_role", "list_entities", "explore_entity", "scan"}
)

SYSTEM = """You check whether retrieved sources contain enough information to fully \
answer a question. Don't answer the question. Decide whether every fact a complete, \
correct answer needs is stated in the sources (a fact that needs combining two \
sources counts as stated if both parts are there). If not, list each missing fact as \
a short search-style description (for example "who was on call the week of November \
16"), most important first. A question the sources can't answer at all is not \
sufficient."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean"},
        "missing": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sufficient", "missing"],
    "additionalProperties": False,
}


def fit(results: list[tuple[str, str]], limit: int = MAX_EVIDENCE_CHARS) -> str:
    """The research results the check reads, as (tool, text), in order and only whole:
    exact results first claim the room, then the latest others that fit. A result cut
    mid-way (a records table missing its first rows) reads as evidence "cut off"."""
    keep: set[int] = set()
    used = 0
    exact = [i for i, (tool, _) in enumerate(results) if tool in EXACT_TOOLS]
    others = [i for i, (tool, _) in enumerate(results) if tool not in EXACT_TOOLS]
    for i in [*reversed(exact), *reversed(others)]:
        if used + len(results[i][1]) <= limit:
            keep.add(i)
            used += len(results[i][1])
    parts = [results[i][1] for i in sorted(keep)]
    if not parts and results:  # one result larger than the room: its end, marked as cut
        tail = results[-1][1][-limit:]
        parts = [
            "(the start of this result was left out for length)\n" + tail[tail.find("\n") + 1 :]
        ]
    if left := len(results) - len(keep) - (1 if not keep and results else 0):
        parts.insert(0, f"({left} earlier search result(s) left out for length.)")
    return "\n\n".join(parts)


async def check(
    question: str, evidence: str | list[tuple[str, str]], on_usage=None
) -> dict[str, Any] | None:
    """{"sufficient": bool, "missing": [...]}, or None if the check couldn't run.
    `evidence` is the research results as (tool, text), or text. `on_usage(model,
    usage)` counts the call in the answer's cost."""
    if not isinstance(evidence, str):
        evidence = fit(evidence)
    elif len(evidence) > MAX_EVIDENCE_CHARS:
        evidence = fit([("", evidence)])
    if not evidence.strip():
        return None
    try:
        verdict = await structured_call(
            model=get_settings().sufficiency_model,
            system=SYSTEM,
            prompt=(f"<question>\n{question}\n</question>\n\n<sources>\n{evidence}\n</sources>"),
            schema=SCHEMA,
            effort="low",
            max_tokens=4000,  # adaptive thinking at low effort: measured, it matters
            on_usage=on_usage,
        )
    except Exception as exc:  # the check is advice; the answer goes on without it
        log.warning("Sufficiency check failed: %s", exc)
        return None
    missing = [m.strip() for m in verdict.get("missing") or [] if m and m.strip()]
    return {"sufficient": bool(verdict.get("sufficient")), "missing": missing[:MAX_MISSING]}


def note(missing: list[str]) -> str:
    """What the answering model is told when facts are missing."""
    items = "\n".join(f"- {m}" for m in missing)
    return (
        "<coverage_check>\nAn automatic check found that the sources so far don't "
        f"state:\n{items}\nSearch for these if they may be in the knowledge base. If "
        "they aren't, say what's missing in the answer instead of guessing.\n"
        "</coverage_check>"
    )
