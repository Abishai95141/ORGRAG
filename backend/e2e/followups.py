"""Replay the scenario's multi-turn conversations and count the follow-up answers that
retract or re-audit an earlier answer unprompted (LOG D66). Run after scenario.py, on its
run directory, with the API up (STRATA_API, default http://localhost:8000).

usage: python e2e/followups.py RUN_DIR REPEATS
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx

RUN = Path(sys.argv[1])
REPEATS = int(sys.argv[2])
USERS = json.loads((RUN / "users.json").read_text())
STATE = json.loads((RUN / "state.json").read_text())
PATTERN = re.compile(
    r"(correct|retract)\w* (to )?(my|the|an) (earlier|previous|first|last)|"
    r"without (any )?(sources|citations)|gave (them|it|those|that) without|"
    r"(earlier|previous|last) (answer|reply)s?\b[^.]{0,80}\b(unverified|not verified|didn't verify|did not verify)",
    re.I,
)
SEQUENCES = {
    "priya": [
        "Which active vendors that can access customer data have a SOC 2 Type II report expiring before March 31, 2027? List each with its business owner and expiry date.",
        "How many active vendors do we have in each category, how many in total, and which category has the most?",
        "Zendesk emailed us about a price increase at renewal. Is that increase allowed under our contract, and by when do we have to give notice if we decide not to renew?",
        "What did we tell the board about opening a Berlin office?",
    ],
    "marcus": [
        "Which incidents this year were first reported by customers rather than caught by our own monitoring? For each one, what was the root cause?",
        "How quickly do I need to report a lost laptop, and to whom?",
        "How many SEV1 incidents have we had this year, and what were they?",
    ],
}


async def main():
    nw = STATE["nw"]
    hits, followups = [], 0
    for who, questions in SEQUENCES.items():
        async with httpx.AsyncClient(
            base_url=os.environ.get("STRATA_API", "http://localhost:8000"), timeout=600
        ) as c:
            await c.post(
                "/api/auth/login",
                json={"email": USERS[who]["email"], "password": USERS["password"]},
            )
            for rep in range(REPEATS):
                conv = (await c.post(f"/api/orgs/{nw}/conversations", json={})).json()["id"]
                for turn, q in enumerate(questions):
                    r = await c.post(
                        f"/api/orgs/{nw}/conversations/{conv}/messages",
                        params={"stream": "false"},
                        json={"content": q},
                    )
                    text = r.json()["message"]["content"]
                    if turn:
                        followups += 1
                        if m := PATTERN.search(text):
                            hits.append(
                                (who, rep, turn, text[max(0, m.start() - 120) : m.end() + 160])
                            )
                    print(
                        f"{who} rep {rep} turn {turn}: {'RETRACTS' if turn and PATTERN.search(text) else 'ok'}",
                        flush=True,
                    )
    print(f"\nretractions: {len(hits)} of {followups} follow-up answers")
    for h in hits:
        print(h)


asyncio.run(main())
