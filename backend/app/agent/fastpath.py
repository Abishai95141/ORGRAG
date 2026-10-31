"""The quick-mode fast path (docs/PLAN-WEEKS-3-5.md step 1.4).

Most quick questions are single lookups the first search already answers; the agent loop
then spends a model turn deciding to search before it can answer. For a question that
looks like a single lookup, the engine runs that first search itself. If the result is
confident, the model answers in one call with tools off; otherwise the loop starts with
those passages already in hand, so the fast path never costs an extra turn."""

import re

from app.core.config import get_settings

MAX_WORDS = 25

# Cues of questions that need several lookups, a full list or a comparison.
MULTI_HOP = re.compile(
    r"\b(?:compare[ds]?|comparison|versus|vs\.?|differ(?:s|ent|ence)?|each|every|all|list|"
    r"how many|which (?:\w+ ){0,2}(?:are|have|has|were|of)|both|either|across|between|"
    r"and (?:who|what|when|how|which)|whose|who (?:approves|owns|signs)|owner|responsible|"
    r"still|latest|current(?:ly)?|changed?|since)\b",
    re.IGNORECASE,
)
# A capitalised word that isn't the question's first word: a named entity, roughly.
_NAME = re.compile(r"(?<![.?!]\s)(?<!^)\b[A-Z][\w&-]+")


def eligible(question: str) -> bool:
    """A short question with no multi-hop cues and at most one named entity."""
    words = question.split()
    if not words or len(words) > MAX_WORDS or MULTI_HOP.search(question):
        return False
    names = {
        m.group(0)
        for m in _NAME.finditer(question.strip())
        if not (m.group(0).isupper() and len(m.group(0)) <= 5)  # acronyms: PTO, BAA, PLAT
    } - {"I"}
    return len(names) <= 1


def confident(scores: list[float], documents: list) -> bool:
    """The first search found the answer: a clear top passage (reranker probability at
    least FAST_PATH_MIN_TOP and at least FAST_PATH_MIN_GAP above the fifth), or the top
    three passages from one document."""
    if not scores:
        return False
    s = get_settings()
    fifth = scores[4] if len(scores) > 4 else 0.0
    if scores[0] >= s.fast_path_min_top and scores[0] - fifth >= s.fast_path_min_gap:
        return True
    return len(documents) >= 3 and len(set(documents[:3])) == 1
