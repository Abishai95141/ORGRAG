"""Generate the scale test: a 60-entry vendor register for a third org ("meridian"),
one Markdown record per vendor, plus list/count questions whose answers span far
more records than one search returns.

Registers like this are templated in real life (one Drive file, Notion row or wiki
page per vendor), so templated records are realistic here. Deterministic (seeded).

    .venv/bin/python eval/scale/generate.py
"""

import json
import random
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "corpus_scale" / "meridian" / "vendor-register"
QUESTIONS = HERE / "questions_scale.jsonl"

PREFIX = [
    "Amber", "Basalt", "Cinder", "Delta", "Ember", "Fjord", "Garnet", "Harbor", "Indigo",
    "Juniper", "Kestrel", "Lumen", "Marlow", "Nimbus", "Onyx", "Pebble", "Quarry", "Rowan",
    "Sable", "Tundra", "Umber", "Vesper", "Willow", "Yarrow", "Zephyr", "Alder", "Birch",
    "Cobalt", "Dune", "Elm",
]  # fmt: skip
SUFFIX = ["Systems", "Analytics", "Labs", "Works", "Partners", "Cloud", "Data", "Health"]
SERVICES = [
    ("payroll processing", True), ("patient messaging", True), ("document shredding", False),
    ("cloud backup", True), ("transcription", True), ("office cleaning", False),
    ("claims clearinghouse", True), ("courier delivery", False), ("e-fax", True),
    ("staff scheduling", False), ("lab results interface", True), ("translation", True),
]  # fmt: skip
OWNERS = ["Priya Nair", "Marcus Hale", "Elena Sato", "Tom Okafor", "Grace Lindqvist"]
RISK = ["Low", "Medium", "High"]
STATUS = ["Executed", "Pending signature", "Expired", "Not required"]


def build(seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    names = rng.sample([f"{p} {s}" for p in PREFIX for s in SUFFIX], 60)
    vendors = []
    for name in names:
        service, phi = rng.choice(SERVICES)
        status = rng.choices(STATUS[:3], weights=[6, 2, 1])[0] if phi else "Not required"
        renewal = date(2026 + rng.randint(0, 1), rng.randint(1, 12), rng.choice([1, 15, 28]))
        vendors.append(
            {
                "name": name,
                "service": service,
                "phi": phi,
                "status": status,
                "risk": rng.choices(RISK, weights=[5, 4, 2])[0],
                "owner": rng.choice(OWNERS),
                "renewal": renewal,
            }
        )
    return vendors


def record(v: dict) -> str:
    signed = v["renewal"].replace(year=v["renewal"].year - 2)
    agreement = {
        "Executed": f"**Executed** — signed {signed:%B %-d, %Y}",
        "Pending signature": "**Pending signature** — sent to the vendor, not yet countersigned",
        "Expired": "**Expired** — lapsed; renewal under negotiation",
        "Not required": "Not required (no patient data)",
    }[v["status"]]
    phi = "Yes — handles protected health information" if v["phi"] else "No"
    return f"""# Vendor record: {v["name"]}

Meridian Care vendor register entry. Maintained by Procurement; questions to the record owner.

| Field | Value |
|---|---|
| Vendor | {v["name"]} |
| Service | {v["service"].capitalize()} |
| Processes PHI | {phi} |
| Business associate agreement | {agreement} |
| Risk tier | {v["risk"]} |
| Record owner | {v["owner"]} |
| Contract renewal | {v["renewal"]:%B %-d, %Y} |

## Notes

{v["name"]} provides {v["service"]} for Meridian clinics. Changes to scope or data access
go through the record owner and Procurement before the contract renewal date.
"""


def main() -> None:
    vendors = build()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.md"):
        old.unlink()
    for v in vendors:
        (OUT / f"{v['name'].lower().replace(' ', '-')}.md").write_text(record(v))

    def title(v):
        return f"Vendor record: {v['name']}"

    def list_q(qid, question, items, note):
        return {
            "id": qid, "category": "list_aggregate", "scope": ["meridian"], "question": question,
            "gold_docs": [title(v) for v in items],
            "facts": [{"fact": v["name"], "any_of": [v["name"]]} for v in items],
            "notes": note,
        }  # fmt: skip

    executed = [v for v in vendors if v["status"] == "Executed"]
    q1_2027 = [v for v in vendors if v["renewal"].year == 2027 and v["renewal"].month <= 3]
    nair_high = [v for v in vendors if v["owner"] == "Priya Nair"]
    high = [v for v in vendors if v["risk"] == "High"]
    questions = [
        list_q("s1", "Which of our vendors have an executed business associate agreement?",
               executed, f"{len(executed)} of 60 records."),
        list_q("s2", "Which vendor contracts come up for renewal in the first quarter of 2027?",
               q1_2027, f"{len(q1_2027)} records; dates spread across 60 records."),
        list_q("s3", "Which vendor records does Priya Nair own?", nair_high,
               f"{len(nair_high)} records."),
        {
            "id": "s4", "category": "list_aggregate", "scope": ["meridian"],
            "question": "How many of our vendors are in the High risk tier?",
            "gold_docs": [title(v) for v in high],
            "facts": [{"fact": f"{len(high)} vendors", "any_of": [str(len(high))]}],
            "notes": "Exact count over 60 records.",
        },
    ]  # fmt: skip
    QUESTIONS.write_text("".join(json.dumps(q) + "\n" for q in questions))
    print(f"{len(vendors)} records → {OUT}")
    for q in questions:
        print(f"  {q['id']}: {len(q['gold_docs'])} gold records — {q['question']}")


if __name__ == "__main__":
    main()
