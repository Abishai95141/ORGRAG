"""Deterministic scoring helpers, kept apart from the harness so tests can import them
(importing the harness repoints the app at the eval database)."""

import json
import re
from typing import Any


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace(" ", " ").replace("‑", "-"))


def substring_hits(answer: str, facts: list[dict[str, Any]]) -> list[bool]:
    norm = normalize(answer)
    return [any(p.lower() in norm for p in f.get("any_of", [])) for f in facts]


def forbidden_hits(answer: str, forbidden: list[str]) -> list[str]:
    """What the answer mustn't state and did: for a leak, which is wrong however it's
    framed. A value an answer may mention to reject it goes in a fact, for the judge."""
    norm = normalize(answer)
    return [p for p in forbidden if p.lower() in norm]


def judge_json(text: str) -> dict[str, Any]:
    """The verdict in a judge's reply: the last JSON object with a `facts` key, so a note
    or a second thought after the first object isn't a parse failure."""
    decoder, found, at = json.JSONDecoder(), None, text.find("{")
    while at != -1:
        try:
            value, end = decoder.raw_decode(text, at)
        except json.JSONDecodeError:
            at = text.find("{", at + 1)
            continue
        if isinstance(value, dict) and "facts" in value:
            found = value
        at = text.find("{", end)
    if found is None:
        raise ValueError(f"no verdict in the reply: {text[:120]!r}")
    return found


def parse_verdict(verdict: dict[str, Any], n_facts: int) -> dict[str, Any]:
    """A judge's reply, one verdict per fact. A reply with too few or too many is refused,
    so it's asked again: padding it would fail a correct answer on the judge's miscount."""
    hits = [bool(x) for x in verdict.get("facts", [])]
    if len(hits) != n_facts:
        raise ValueError(f"judge gave {len(hits)} verdicts for {n_facts} facts")
    return {
        "facts": hits,
        "abstained": bool(verdict.get("abstained")),
        "contradiction": bool(verdict.get("contradiction")),
    }
