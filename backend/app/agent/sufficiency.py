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

MAX_EVIDENCE_CHARS = 24_000  # the latest results are kept when there's more
MAX_MISSING = 3

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


async def check(question: str, evidence: str, on_usage=None) -> dict[str, Any] | None:
    """{"sufficient": bool, "missing": [...]}, or None if the check couldn't run.
    `on_usage(model, usage)` counts the call in the answer's cost."""
    if not evidence.strip():
        return None
    try:
        verdict = await structured_call(
            model=get_settings().sufficiency_model,
            system=SYSTEM,
            prompt=(
                f"<question>\n{question}\n</question>\n\n"
                f"<sources>\n{evidence[-MAX_EVIDENCE_CHARS:]}\n</sources>"
            ),
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
