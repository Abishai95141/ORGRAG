"""Grounding metrics: is each statement in an answer supported by what was retrieved,
and do its citations point at passages that support it? (ALCE-style; roadmap §1.)

The answer is split into statements deterministically (sentences, list items, table
rows), each keeping the `[n]` citations it carries. One judge call per answer then
labels every statement against the passages the model actually saw:

* `claim`: does it assert something that needs support? (Framing, "I couldn't find
  …" and restatements don't.)
* `supported`: is it supported by the retrieved passages taken together?
  → **faithfulness** = supported claims / claims.
* `cited_support`: is it supported by the passages it cites? (False when uncited.)
  → **citation recall** = claims fully supported by their own citations / claims.
* per citation: does that passage support at least part of the statement?
  → **citation precision** = supporting citations / citations on claims. A citation
  to a number that isn't a source counts as not supporting.

Pure functions: the harness makes the judge call (with its cache), so this module
imports nothing from the app and is tested without a model.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

PROMPT_VERSION = "grounding-v1"
MAX_STATEMENTS = 60
MAX_PASSAGE_CHARS = 48_000  # all passages together; cited ones go first and whole

_CITE = re.compile(r"\[(\d{1,3})\]")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
# Citations written after the full stop ("… approval. [1]") belong to that sentence.
_TRAILING_CITES = re.compile(r"([.!?])((?:\s*\[\d{1,3}\])+)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“*(\[])")

SYSTEM = """You check whether a knowledge assistant's answer is grounded in the \
passages it retrieved. You get the question, numbered passages [n], the full answer, \
and the answer split into numbered statements with the citations each carries. Judge \
each statement in the context of the whole answer.

For every statement decide:
- "claim": does it assert something factual that needs support? false for framing \
("Here's what I found"), saying information wasn't found, offers or questions to the \
user, and headings.
- "supported": is it fully supported by the passages taken together (any of them)? \
Paraphrase and simple arithmetic over passage facts count as supported; anything \
added, more specific, or contradicting does not.
- "cited_support": is it fully supported by the passages it cites, taken together? \
false if it cites nothing.
- "citations": for each citation on the statement, does that passage support at \
least part of the statement?
For non-claims, set supported and cited_support to true and still judge citations.

Reply with only JSON:
{"statements": [{"i": 1, "claim": true, "supported": true, "cited_support": true, \
"citations": {"3": true}}]}"""


@dataclass
class Statement:
    text: str
    refs: list[int]


@dataclass
class Grounding:
    statements: int = 0
    claims: int = 0
    supported: int = 0
    cited_supported: int = 0
    citations: int = 0
    good_citations: int = 0
    dangling: list[int] = field(default_factory=list)  # cited refs that aren't sources
    labels: list[dict[str, Any]] = field(default_factory=list)

    @property
    def faithfulness(self) -> float | None:
        return self.supported / self.claims if self.claims else None

    @property
    def citation_recall(self) -> float | None:
        return self.cited_supported / self.claims if self.claims else None

    @property
    def citation_precision(self) -> float | None:
        return self.good_citations / self.citations if self.citations else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "statements": self.statements,
            "claims": self.claims,
            "supported": self.supported,
            "cited_supported": self.cited_supported,
            "citations": self.citations,
            "good_citations": self.good_citations,
            "dangling": self.dangling,
            "labels": self.labels,
        }


def _clean(text: str) -> str:
    text = _CITE.sub("", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)  # "approval [1]." → "approval."
    return re.sub(r"\s+", " ", text).strip(" :")


def statements(answer: str) -> list[Statement]:
    """Sentences, list items and table rows, each with the citations it carries."""
    out: list[Statement] = []
    lines = answer.replace("\r\n", "\n").split("\n")
    in_code = False
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line or line.startswith("#") or re.fullmatch(r"[-*_]{3,}", line):
            continue
        if _TABLE_RULE.match(line):
            continue
        if line.startswith("|"):
            # A header row is followed by the |---| rule; it isn't a statement.
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if _TABLE_RULE.match(nxt):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            text = " | ".join(c for c in cells if c)
            if _clean(text):
                out.append(Statement(_clean(text), _refs(text)))
            continue
        line = _BULLET.sub("", line).lstrip("> ")
        line = _TRAILING_CITES.sub(r"\2\1", line)
        for sentence in _SENTENCE_END.split(line):
            if _clean(sentence):
                out.append(Statement(_clean(sentence), _refs(sentence)))
    return out[:MAX_STATEMENTS]


def _refs(text: str) -> list[int]:
    seen: list[int] = []
    for m in _CITE.finditer(text):
        if int(m.group(1)) not in seen:
            seen.append(int(m.group(1)))
    return seen


def passage_block(passages: dict[int, str], cited: set[int]) -> str:
    """Cited passages first and whole, then the rest while the budget lasts."""
    parts, used = [], 0
    for ref in sorted(passages, key=lambda r: (r not in cited, r)):
        text = passages[ref]
        room = MAX_PASSAGE_CHARS - used
        if room <= 200:
            break
        if len(text) > room:
            text = text[:room] + " …"
        parts.append(f"[{ref}]\n{text}")
        used += len(text)
    return "\n\n".join(parts)


def build_prompt(
    question: str, answer: str, items: list[Statement], passages: dict[int, str]
) -> str:
    cited = {r for s in items for r in s.refs}
    numbered = "\n".join(
        f"{i}. {s.text}" + (f"  (cites {', '.join(f'[{r}]' for r in s.refs)})" if s.refs else "")
        for i, s in enumerate(items, 1)
    )
    return (
        f"Question: {question}\n\nPassages:\n{passage_block(passages, cited)}\n\n"
        f"Full answer:\n{answer}\n\nStatements:\n{numbered}"
    )


def score(items: list[Statement], verdict: dict[str, Any], sources: set[int]) -> Grounding:
    by_i = {
        int(s.get("i", n)): s
        for n, s in enumerate(verdict.get("statements") or [], 1)
        if isinstance(s, dict)
    }
    g = Grounding(statements=len(items))
    for i, st in enumerate(items, 1):
        label = by_i.get(i, {})
        # A statement the judge skipped counts as an unsupported claim, not a free pass.
        claim = bool(label.get("claim", True))
        supported = claim and bool(label.get("supported", False))
        cited = claim and bool(st.refs) and bool(label.get("cited_support", False))
        marks = {str(k): bool(v) for k, v in (label.get("citations") or {}).items()}
        g.labels.append(
            {"text": st.text, "refs": st.refs, "claim": claim, "supported": supported,
             "cited_support": cited, "citations": marks}
        )  # fmt: skip
        if not claim:
            continue
        g.claims += 1
        g.supported += supported
        g.cited_supported += cited
        for ref in st.refs:
            g.citations += 1
            if ref not in sources:
                if ref not in g.dangling:
                    g.dangling.append(ref)
                continue
            g.good_citations += marks.get(str(ref), False)
    return g


def parse_json(text: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"judge reply has no JSON object: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """Micro-averaged over claims (and citations) across all graded answers."""
    gs = [r["grounding"] for r in rows if r.get("grounding")]
    claims = sum(g["claims"] for g in gs)
    cites = sum(g["citations"] for g in gs)

    def ratio(num: int, den: int) -> float | None:
        return round(num / den, 3) if den else None

    return {
        "faithfulness": ratio(sum(g["supported"] for g in gs), claims),
        "cite_recall": ratio(sum(g["cited_supported"] for g in gs), claims),
        "cite_precision": ratio(sum(g["good_citations"] for g in gs), cites),
    }
