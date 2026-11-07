"""The hard set's questions, computed from the world's truth.

Each family below picks instances from `World` and writes a question, its required facts
and its `hops`: the document and exact quote behind every step, so eval/hard/check.py can
prove the gold from the stored text. A count or sum also carries its `derivation`.

A family's first instance goes to the dev split, the rest to test. Dev is what the
generator was tuned against; test was never looked at while tuning, so its score isn't
inflated by choosing questions the system happened to fail.

    .venv/bin/python -m eval.hard.questions     # writes eval/hard/questions*.jsonl
"""

import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from eval.hard import render as r
from eval.hard.world import (
    END,
    FREEZE,
    POLICIES,
    RETENTION,
    RETENTION_WITHDRAWN,
    SEED,
    SLO,
    STANDBY,
    TEAMS,
    Doc,
    Incident,
    World,
    build,
    date_forms,
    day_date,
    long_date,
    monday_of,
    month_name,
    months,
)

HERE = Path(__file__).resolve().parent
CONTRACTS = "contract values 2026"
WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve"]  # fmt: skip
STAFF = "noor.aziz"  # a member: org-wide collections only
FINANCE = "owen.marsh"
PEOPLE = "leah.stone"
LEADS = "dev.patel"
SECURITY = "ana.ruiz"
ASKERS = {
    STAFF: ("Noor Aziz", []),
    FINANCE: ("Owen Marsh", [r.FINANCE]),
    PEOPLE: ("Leah Stone", [r.PEOPLE]),
    LEADS: ("Dev Patel", [r.LEADERSHIP]),
    SECURITY: ("Ana Ruiz", [r.SECURITY]),
}


def number_forms(n: int) -> list[str]:
    return [str(n)] + ([WORDS[n]] if 0 <= n < len(WORDS) else [])


def money_forms(n: int) -> list[str]:
    return [f"£{n:,}", f"£{n}", f"{n:,}", str(n)]


def fact(text: str, *forms: str) -> dict:
    return {"fact": text, "any_of": list(dict.fromkeys(forms))}


def person_fact(name: str, text: str | None = None) -> dict:
    return fact(text or name, name)


@dataclass
class Corpus:
    docs: list[Doc]

    def hop(self, quote: str, title: str | None = None) -> dict:
        """The document holding a quote. Every hop is proved again against the database."""
        for d in self.docs:
            if quote in d.text and (title is None or d.title == title):
                return {"doc": d.title, "quote": quote}
        raise AssertionError(f"no document says {quote!r} (title {title!r})")


class Builder:
    def __init__(self, w: World, docs: list[Doc]):
        self.w, self.c = w, Corpus(docs)
        self.rng = random.Random(SEED + 1)
        self.rows: list[dict] = []
        self.seen: Counter = Counter()

    def add(
        self,
        family: str,
        category: str,
        question: str,
        facts: list[dict],
        hops: list[dict],
        notes: str = "",
        **extra,
    ) -> None:
        self.seen[family] += 1
        n = self.seen[family]
        titles = list(dict.fromkeys(h["doc"] for h in hops))
        self.rows.append(
            {
                "id": f"{family}-{n}",
                "category": category,
                "scope": ["strata"],
                "question": question,
                "gold_docs": titles,
                "facts": facts,
                "notes": notes,
                "hops": hops,
                "split": "dev" if n == 1 else "test",
                **extra,
            }
        )

    # ── shared evidence ──────────────────────────────────────────────────────
    def incident_hop(self, i: Incident) -> dict:
        title = r.postmortem_title(i)
        quote = day_date(i.day) if i.style == "prose" else long_date(i.day)
        return self.c.hop(quote, title)

    def directory_hop(self, name: str, d: date) -> dict:
        """The evidence for which team someone was in on a date: the last snapshot, or
        the announcement if they moved after it."""
        w = self.w
        p = w.people[name]
        team = p.team_on(d)
        snap = max(x for x in r.directory_dates() if x <= d)
        if p.team_on(snap) == team:
            row = f"| {p.name} | {r.role_on(w, p, snap)} | {team} | {p.base} |"
            return self.c.hop(row, r.directory_title(snap))
        when = max(x for x, t in p.moves if t == team and x <= d)
        return self.c.hop(f"{p.name} joins {team} from {day_date(when)}")

    def rota_hop(self, week: date) -> dict:
        w = self.w
        a, b = (w.people[n].initial for n in w.rota[week])
        return self.c.hop(f"| {long_date(week)} | {a} | {b} |", r.rota_title(week.replace(day=1)))

    def swap_hop(self, week: date, slot: str) -> dict | None:
        for wk, s, *_ in self.w.swaps:
            if wk == week and s == slot:
                return self.c.hop(f"I'll take it. Rota is updated for {long_date(week)}.")
        return None

    def alias_hops(self, service: str, posted: str) -> list[dict]:
        """A post that used the project name needs the page that says what it means."""
        s = self.w.services[service]
        if s.codename not in posted:
            return []
        return [self.c.hop(f"Also known as: {s.codename}", r.service_title(service))]

    def posted_hops(self, key: tuple, service: str) -> list[dict]:
        text = self.w.posted[key]
        return [self.c.hop(text), *self.alias_hops(service, text)]

    def handover_hops(self, service: str, when: date) -> list[dict]:
        return self.posted_hops(("handover", service, when), service)

    def owner_hops(self, s, d: date) -> list[dict]:
        """Who owned a service on a date: the handover that gave it to them, or its page."""
        taken = [when for when, _ in s.owners[1:] if when <= d]
        if taken:
            return self.handover_hops(s.name, taken[-1])
        first = s.owners[0][1]
        if s.owner_on(s.page_reviewed) == first:
            return [self.c.hop(f"Owner: {first}", r.service_title(s.name))]
        return self.handover_hops(s.name, s.owners[1][0])  # "moves from <first> to ..."

    def renewal_hops(self, v) -> list[dict]:
        title = r.variation_title(v.name)
        if not v.variation:
            return [
                self.c.hop(f"| Renewal date | {long_date(v.renewal)} |", r.vendor_title(v.name))
            ]
        if v.relative:
            return [
                self.c.hop(f"| Renewal date | {long_date(v.renewal)} |", r.vendor_title(v.name)),
                self.c.hop("extended by 12 months from the current renewal date", title),
            ]
        return [self.c.hop(f"to {long_date(v.true_renewal)}", title)]

    def major(self, period=lambda d: True) -> list[Incident]:
        return [i for i in self.w.incidents if i.has_postmortem and period(i.day)]

    def unique_handle(self, i: Incident) -> bool:
        """Is "<service>, <cause>, <month>" enough to name this incident?"""
        same = [
            x
            for x in self.w.incidents
            if x.service == i.service
            and x.cause == i.cause
            and (x.day.year, x.day.month) == (i.day.year, i.day.month)
        ]
        return len(same) == 1

    def action_hop(self, i: Incident, a) -> dict:
        text = a.text[0].lower() + a.text[1:] if i.style == "prose" else a.text
        return self.c.hop(text, r.postmortem_title(i))

    def reassign_hop(self, i: Incident, a) -> dict:
        return self.c.hop(f'{i.id}: I can\'t get to "{a.text}". {a.reassigned[1]} has agreed')

    def severity_hop(self, i: Incident) -> dict:
        quote = {
            "prose": f"severity {i.severity} incident",
            "lines": f"Severity: {i.severity}",
            "table": f"| Severity | {i.severity} |",
        }[i.style]
        return self.c.hop(quote, r.postmortem_title(i))


# ── chains: four or more steps, each in a different kind of document ─────────
def chain_oncall(b: Builder) -> None:
    w = b.w
    weeks = {wk for wk, s, *_ in w.swaps if s == "primary"}
    pool = [i for i in b.major() if b.unique_handle(i)]
    pool.sort(key=lambda i: monday_of(i.day) not in weeks)
    forms = [
        "Who was the primary on call when {s} went down with {c} in {m}, and which team were "
        "they in at the time?",
        "{s} had {c} in {m}. Which person was primary on call that week, and what team did they "
        "belong to then?",
    ]
    for i in pool[:7]:
        week = monday_of(i.day)
        who = w.on_call(i.day)
        team = w.people[who].team_on(i.day)
        hops = [b.incident_hop(i), b.rota_hop(week)]
        if swap := b.swap_hop(week, "primary"):
            hops.append(swap)
        hops.append(b.directory_hop(who, i.day))
        b.add(
            "chain-oncall",
            "chain",
            b.rng.choice(forms).format(s=i.service, c=i.cause_short, m=month_name(i.day)),
            [person_fact(who, f"{who} was primary on call"), fact(f"In {team}", team)],
            hops,
            f"{i.id} → week of {week} → rota → swaps in #ps-oncall → directory",
        )


def chain_handover(b: Builder) -> None:
    w = b.w
    found = 0
    for i in b.major():
        s = w.services[i.service]
        if s.owner_on(END) == s.owner_on(i.day) or not b.unique_handle(i):
            continue
        when, team = s.owners[-1]
        lead = w.lead_on(team, END)
        base = w.people[lead].base
        snap = r.directory_dates()[-1]
        row = f"| {lead} | {r.role_on(w, w.people[lead], snap)} | {team} | {base} |"
        b.add(
            "chain-handover",
            "chain",
            f"Which team runs the service that had {i.cause_short} on {long_date(i.day)} now, who "
            f"leads that team, and where are they based?",
            [fact(f"Run by {team}", team), person_fact(lead), fact(f"Based at {base}", base)],
            [
                b.incident_hop(i),
                *b.handover_hops(s.name, when),
                b.c.hop(f"Team lead: {lead}", r.team_title(team)),
                b.c.hop(row, r.directory_title(snap)),
            ],
            "postmortem → handover (Slack; the service page may be stale) → charter → directory",
        )
        found += 1
        if found == 6:
            return


def chain_escalation(b: Builder) -> None:
    """The runbook says page the owning team's lead: both halves of that may have moved."""
    w = b.w
    stale = [
        s for s in w.services.values() if len(s.owners) > 1 and s.page_reviewed < s.owners[-1][0]
    ]
    for s in stale[:5]:
        when, team = s.owners[-1]
        lead = w.lead_on(team, END)
        b.add(
            "chain-escalation",
            "chain",
            f"{s.name} is down. Its runbook says to page the owning team's lead: who is that "
            f"today?",
            [person_fact(lead, f"{lead}, who leads {team}")],
            [
                b.c.hop("Page the owning team's lead", r.runbook_title(s.name)),
                *b.handover_hops(s.name, when),
                b.c.hop(f"Team lead: {lead}", r.team_title(team)),
            ],
            "the runbook and service page predate the handover",
        )


def chain_handover_oncall(b: Builder) -> None:
    w = b.w
    moved = [(s, when, team) for s in w.services.values() for when, team in s.owners[1:]]
    swapped = {wk for wk, slot, *_ in w.swaps if slot == "primary"}
    moved.sort(key=lambda m: (monday_of(m[1]) not in swapped, m[0].name))
    for s, when, team in moved[:4]:
        week = monday_of(when)
        who = w.on_call(when)
        hops = [*b.handover_hops(s.name, when), b.rota_hop(week)]
        if swap := b.swap_hop(week, "primary"):
            hops.append(swap)
        b.add(
            "chain-handover-oncall",
            "chain",
            f"Who was primary on call in the week {s.name} moved to {team}?",
            [person_fact(who, f"{who} was primary on call")],
            hops,
            "handover post → effective date → that week's rota → swaps",
        )


def chain_action(b: Builder) -> None:
    w = b.w
    found = 0
    for i in b.major():
        for a in i.actions:
            if not a.reassigned or a.closed or not b.unique_handle(i) or found == 3:
                continue
            who = a.owner_on(END)
            team = w.people[who].team_on(END)
            b.add(
                "chain-action",
                "chain",
                f"After {i.service} had {i.cause_short} in {month_name(i.day)}, who ended up "
                f"owning the action to {a.text[0].lower()}{a.text[1:]}, and which team are they "
                f"in now?",
                [person_fact(who, f"{who} owns it now"), fact(f"In {team}", team)],
                [
                    b.action_hop(i, a),
                    b.reassign_hop(i, a),
                    b.directory_hop(who, END),
                ],
                f"{i.id}: {a.owner} handed it over in Slack",
            )
            found += 1


def chain_decision(b: Builder) -> None:
    w = b.w
    firsts = [
        d
        for d in w.decisions.values()
        if d.superseded_by and not any(o.superseded_by == d.id for o in w.decisions.values())
    ]
    for first in b.rng.sample(firsts, min(3, len(firsts))):
        nxt = w.decisions[first.superseded_by]
        team = w.people[nxt.decider].team_on(nxt.decided)
        topic = first.title.split(" for the ", 1)[1]
        b.add(
            "chain-decision",
            "chain",
            f"We used to use {first.choice} for the {topic}. What did we move to next, who made "
            f"that decision, and which team were they in when they made it?",
            [
                fact(f"Replaced by {nxt.choice}", nxt.choice),
                person_fact(nxt.decider),
                fact(f"In {team}", team),
            ],
            [
                b.c.hop(f"We chose {first.choice}", r.decision_title(first)),
                b.c.hop(f"Replaces {first.id}.", r.decision_title(nxt)),
                b.directory_hop(nxt.decider, nxt.decided),
            ],
            "only the newer decision says what it replaced",
        )


def rate_on(d: date) -> tuple[int, int, int]:
    """(version, weekly, per call-out) of the standby policy in force on d."""
    version, weekly, callout = 0, 0, 0
    for n, (when, a, c) in enumerate(STANDBY, 1):
        if when <= d:
            version, weekly, callout = n, a, c
    return version, weekly, callout


def chain_standby(b: Builder) -> None:
    w = b.w
    seen: set[tuple[str, date]] = set()
    for week, _, new, _, old in w.swaps:
        for name in (new, old):
            m = week.replace(day=1)
            weeks = [
                wk
                for wk in sorted(w.rota)
                if wk.replace(day=1) == m and name in (w.on_call(wk), w.on_call(wk, "secondary"))
            ]
            if len(weeks) < 2 or len(seen) >= 6 or (name, m) in seen:
                continue
            seen.add((name, m))
            total = sum(rate_on(wk)[1] for wk in weeks)
            version = rate_on(weeks[-1])[0]
            hops = [b.rota_hop(wk) for wk in sorted(w.rota) if wk.replace(day=1) == m]
            for wk, _, gained, _, lost in w.swaps:
                if wk.replace(day=1) == m and name in (gained, lost):
                    hops.append(b.c.hop(f"Rota is updated for {long_date(wk)}"))
                    hops.append(b.c.hop(f"week commencing {long_date(wk)}?"))
            hops.append(
                b.c.hop(
                    f"standby allowance of £{rate_on(weeks[-1])[1]}",
                    r.policy_title("Standby allowance policy", version),
                )
            )
            b.add(
                "chain-standby",
                "chain",
                f"How many weeks was {name} on the on-call rota in {month_name(m)} (weeks "
                f"commencing that month), and how much standby allowance did that earn before "
                f"any call-outs?",
                [
                    fact(f"{len(weeks)} weeks on the rota after swaps", *number_forms(len(weeks))),
                    fact(f"£{total} in total", *money_forms(total)),
                ],
                hops,
                "rota (initials) → swaps in Slack → the policy version in force that month",
                derivation={
                    "op": "sum",
                    "result": total,
                    "items": [{"label": wk.isoformat(), "value": rate_on(wk)[1]} for wk in weeks],
                },
            )


def chain_lead_pay(b: Builder) -> None:
    """Four documents and some arithmetic, from one description of an incident."""
    w = b.w
    picked = 0
    for i in reversed(b.major()):
        m = monday_of(i.day).replace(day=1)
        weeks = [
            wk
            for wk in sorted(w.rota)
            if wk.replace(day=1) == m and i.lead in (w.on_call(wk), w.on_call(wk, "secondary"))
        ]
        if not weeks or not b.unique_handle(i) or picked >= 5:
            continue
        total = sum(rate_on(wk)[1] for wk in weeks)
        version = rate_on(weeks[-1])[0]
        hops = [b.incident_hop(i), *[b.rota_hop(wk) for wk in weeks]]
        for wk in weeks:
            for slot in ("primary", "secondary"):
                if swap := b.swap_hop(wk, slot):
                    hops.append(swap)
        hops.append(
            b.c.hop(
                f"standby allowance of £{rate_on(weeks[-1])[1]}",
                r.policy_title("Standby allowance policy", version),
            )
        )
        b.add(
            "chain-lead-pay",
            "chain",
            f"{i.service} had {i.cause_short} in {month_name(i.day)}. How much standby allowance "
            f"did the person who led that response earn for the weeks commencing in "
            f"{month_name(m)}, before call-outs?",
            [fact(f"£{total}", *money_forms(total))],
            hops,
            f"{i.lead} led it. postmortem → rota in that month → swaps → the policy version then "
            f"in force",
            derivation={
                "op": "sum",
                "result": total,
                "items": [{"label": wk.isoformat(), "value": rate_on(wk)[1]} for wk in weeks],
            },
        )
        picked += 1


def chain_dependency(b: Builder) -> None:
    w = b.w
    found = 0
    for s in w.services.values():
        for dep in s.depends_on:
            sev1 = [i for i in b.major() if i.service == dep and i.severity == 1]
            for i in sev1:
                m = (i.day.year, i.day.month)
                others = [
                    x
                    for x in b.major()
                    if x.service in s.depends_on
                    and x.severity == 1
                    and (x.day.year, x.day.month) == m
                    and x is not i
                ]
                if others or found >= 4:
                    continue
                b.add(
                    "chain-dependency",
                    "chain",
                    f"One of the services {s.name} depends on had a severity 1 incident in "
                    f"{month_name(i.day)}. Which service was it, what caused the incident, and "
                    f"who led the response?",
                    [
                        fact(dep, dep),
                        fact(i.cause_text, i.cause_short, i.cause_text),
                        person_fact(i.lead),
                    ],
                    [
                        b.c.hop(f"Depends on: {', '.join(s.depends_on)}", r.service_title(s.name)),
                        b.c.hop(f"{i.id} ({dep})", r.report_title(i.day.replace(day=1))),
                        b.incident_hop(i),
                    ],
                    "service page → the month's report → postmortem",
                )
                found += 1
                break


# ── aggregates: the answer is spread over many documents ─────────────────────
def in_year(y: int):
    return lambda d: d.year == y


def agg_count(b: Builder) -> None:
    periods = [
        ("2025", in_year(2025)),
        ("2026 so far", in_year(2026)),
        ("the first half of 2026", lambda d: date(2026, 1, 1) <= d < date(2026, 7, 1)),
        ("the second half of 2025", lambda d: date(2025, 7, 1) <= d < date(2026, 1, 1)),
        ("the first quarter of 2026", lambda d: date(2026, 1, 1) <= d < date(2026, 4, 1)),
    ]
    for label, cond in periods:
        hits = [i for i in b.major(cond) if i.severity == 1]
        b.add(
            "agg-count",
            "aggregate",
            f"How many severity 1 incidents did Port Systems have in {label}? List their "
            f"references.",
            [fact(f"{len(hits)} incidents", *number_forms(len(hits)))]
            + [fact(i.id, i.id) for i in hits],
            [b.severity_hop(i) for i in hits],
            "postmortems come in three formats, so records see only some of them",
            derivation={
                "op": "count",
                "result": len(hits),
                "items": [{"label": i.id, "value": 1} for i in hits],
            },
        )


def agg_owner(b: Builder) -> None:
    w = b.w
    for team in TEAMS:
        hits = [i for i in b.major(in_year(2026)) if w.services[i.service].owner_on(i.day) == team]
        if len(hits) < 2 or b.seen["agg-owner"] >= 4:
            continue
        hops = [b.severity_hop(i) for i in hits]
        for i in hits:
            hops += b.owner_hops(w.services[i.service], i.day)
        b.add(
            "agg-owner",
            "aggregate",
            f"How many severity 1 or 2 incidents in 2026 were on services that {team} owned at "
            f"the time?",
            [fact(f"{len(hits)} incidents", *number_forms(len(hits)))],
            hops,
            "a join: incidents × who owned each service on the day",
            derivation={
                "op": "count",
                "result": len(hits),
                "items": [{"label": i.id, "value": 1} for i in hits],
            },
        )


def agg_impact(b: Builder) -> None:
    by_service: dict[str, list[Incident]] = defaultdict(list)
    for i in b.major():
        by_service[i.service].append(i)
    picked = [
        (name, rows) for name, rows in by_service.items()
        if len(rows) >= 2 and any(i.corrected_impact is not None for i in rows)
    ]  # fmt: skip
    picked += [(n, x) for n, x in by_service.items() if len(x) >= 3 and (n, x) not in picked]
    for name, rows in picked[:4]:
        total = sum(i.true_impact for i in rows)
        label = rows[0].impact_label
        hops = []
        for i in rows:
            hops.append(b.c.hop(f"{i.impact} {label}", r.postmortem_title(i)))
            if i.corrected_impact is not None:
                hops.append(b.c.hop(f"Correction for {i.id}"))
        b.add(
            "agg-impact",
            "aggregate",
            f"Across all severity 1 and 2 incidents on {name}, what is the total number of "
            f"{label}?",
            [fact(f"{total} in total", *money_forms(total)[2:])],
            hops,
            "sum over postmortems, with corrections that exist only in Slack",
            derivation={
                "op": "sum",
                "result": total,
                "items": [{"label": i.id, "value": i.true_impact} for i in rows],
            },
        )


def agg_open_actions(b: Builder) -> None:
    owners: dict[str, list[tuple[Incident, object]]] = defaultdict(list)
    for i in b.major():
        for a in i.actions:
            if a.closed is None:
                owners[a.owner_on(END)].append((i, a))
    ranked = sorted(owners.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for name, rows in ranked[:6]:
        hops = []
        for i, a in rows:
            hops.append(b.action_hop(i, a))
            if a.reassigned:
                hops.append(b.reassign_hop(i, a))
        for i in b.major():
            for a in i.actions:
                if a.closed and a.owner == name:  # theirs, and finished: the gold shows that too
                    hops.append(b.c.hop(f'{i.id}: done. "{a.text}"'))
        b.add(
            "agg-actions",
            "aggregate",
            f"Which postmortem actions does {name} own that haven't been finished?",
            [fact(f"{len(rows)} open actions", *number_forms(len(rows)))]
            + [fact(f"{a.text} ({i.id})", a.text) for i, a in rows],
            hops,
            "open = no 'done' post in #ps-incidents; some reached this owner by a handover",
        )


def agg_slo(b: Builder) -> None:
    w = b.w
    corrected = sorted({n for _, n, _, _ in w.corrections})
    names = (corrected + [n for n in w.services if n not in corrected])[:7]
    for name in names:
        s = w.services[name]
        year = 2026 if any(m.year == 2026 and n == name for m, n, _, _ in w.corrections) else 2025
        ms = [m for m in months() if m.year == year]
        missed = [m for m in ms if w.true_availability(m, name) < SLO[s.tier]]
        if not missed or len(missed) > 8:
            continue
        hops = [
            b.c.hop(f"| {name} | {s.tier} | {SLO[s.tier]}% | {w.availability[(m, name)]:.2f}% |",
                    r.report_title(m))
            for m in ms
        ]  # fmt: skip
        for m, n, _, _ in w.corrections:
            if n == name and m.year == year:
                hops += b.posted_hops(("availability", m, n), n)
        b.add(
            "agg-slo",
            "aggregate",
            f"In which months of {year} did {name} miss its availability target?",
            [fact(f"{len(missed)} month{'s' * (len(missed) > 1)}", *number_forms(len(missed)))]
            + [fact(month_name(m), f"{m:%B}", f"{m:%b}") for m in missed],
            hops,
            "one row in each monthly report, and a correction posted weeks later in Slack",
        )


def agg_renewals(b: Builder) -> None:
    w = b.w
    windows = [
        ("January to March 2027", date(2027, 1, 1), date(2027, 4, 1)),
        ("April to June 2027", date(2027, 4, 1), date(2027, 7, 1)),
        ("July to September 2027", date(2027, 7, 1), date(2027, 10, 1)),
        ("October to December 2027", date(2027, 10, 1), date(2028, 1, 1)),
    ]
    for label, lo, hi in windows:
        hits = [v for v in w.vendors.values() if lo <= v.true_renewal < hi]
        if not 2 <= len(hits) <= 10:
            continue
        hops = [h for v in hits for h in b.renewal_hops(v)]
        b.add(
            "agg-renewals",
            "aggregate",
            f"Which vendor contracts come up for renewal between {label}?",
            [fact(f"{len(hits)} contracts", *number_forms(len(hits)))]
            + [fact(v.name, v.name) for v in hits],
            hops,
            "the January register is stale for contracts varied since",
            derivation={
                "op": "count",
                "result": len(hits),
                "items": [{"label": v.name, "value": 1} for v in hits],
            },
        )


def agg_cause(b: Builder) -> None:
    keys = Counter(i.cause for i in b.w.incidents)
    for key, _ in keys.most_common()[:3]:
        hits = [i for i in b.w.incidents if i.cause == key]
        short = hits[0].cause_short
        hops = [
            b.c.hop(i.cause_text[1:], r.postmortem_title(i))
            if i.has_postmortem
            else b.c.hop(f"{i.id} ({i.service}): {i.cause_text}")
            for i in hits
        ]
        b.add(
            "agg-cause",
            "aggregate",
            f"How many incidents have been caused by {short}?",
            [fact(f"{len(hits)} incidents", *number_forms(len(hits)))],
            hops,
            "severity 3 incidents exist only as a line in #ps-incidents",
            derivation={
                "op": "count",
                "result": len(hits),
                "items": [{"label": i.id, "value": 1} for i in hits],
            },
        )


def agg_lead(b: Builder) -> None:
    leaders = Counter(i.lead for i in b.w.incidents if i.day.year == 2025)
    for name, _ in leaders.most_common()[:3]:
        hits = [i for i in b.w.incidents if i.day.year == 2025 and i.lead == name]
        hops = [
            b.c.hop(
                f"{name} led the response" if i.style == "prose" else name, r.postmortem_title(i)
            )
            if i.has_postmortem
            else b.c.hop(f"{i.id} ({i.service}): {i.cause_text}")
            for i in hits
        ]
        b.add(
            "agg-lead",
            "aggregate",
            f"How many incidents did {name} lead in 2025?",
            [fact(f"{len(hits)} incidents", *number_forms(len(hits)))],
            hops,
            "minor incidents name their lead only as the Slack post's author",
            derivation={
                "op": "count",
                "result": len(hits),
                "items": [{"label": i.id, "value": 1} for i in hits],
            },
        )


# ── as of a date: what was true then, not what is true now ───────────────────
def asof_owner(b: Builder) -> None:
    w = b.w
    moved = [s for s in w.services.values() if len(s.owners) > 1]
    for s in b.rng.sample(moved, 5):
        when, new = s.owners[-1]
        on = when - timedelta(days=b.rng.randint(20, 60))
        old = s.owner_on(on)
        b.add(
            "asof-owner",
            "as_of",
            f"Which team owned {s.name} on {long_date(on)}?",
            [fact(old, old)],
            b.handover_hops(s.name, when),
            "the service moved after this date",
        )


def asof_announced(b: Builder) -> None:
    """A date between the announcement and the day it took effect: it says "from the 1st"."""
    w = b.w
    found = 0
    for s in w.services.values():
        for when, _ in s.owners[1:]:
            text = w.posted[("handover", s.name, when)]
            if "1st" in text and found < 6:
                on = when - timedelta(days=2)
                old = s.owner_on(on)
                b.add(
                    "asof-announced",
                    "as_of",
                    f"Which team was responsible for {s.name} on {long_date(on)}?",
                    [fact(old, old)],
                    b.handover_hops(s.name, when),
                    "announced before this date, effective after it, and the post says only "
                    "'the 1st'; the channel date gives the month",
                )
                found += 1


def asof_event(b: Builder) -> None:
    """Dated by something that happened: one more step to find the date."""
    w = b.w
    found = 0
    for s in w.services.values():
        if len(s.rto_hours) < 2 or s.page_reviewed >= s.rto_hours[-1][0]:
            continue
        when, hours = s.rto_hours[-1]
        before = [
            i for i in b.major() if i.service == s.name and i.day < when and b.unique_handle(i)
        ]
        if before and found < 2:
            i = before[-1]
            old = s.rto_on(i.day)
            b.add(
                "asof-event-rto",
                "as_of",
                f"What was {s.name}'s recovery time target when it had {i.cause_short} in "
                f"{month_name(i.day)}?",
                [fact(f"{old} hours", f"{old} hours", f"{old}-hour", f"{old} hour")],
                [
                    b.incident_hop(i),
                    b.c.hop(f"Recovery time target: {old} hours", r.runbook_title(s.name)),
                    *b.posted_hops(("rto", s.name, when), s.name),
                ],
                "the target changed after the incident, in a Slack post",
            )
            found += 1
    moved = [(s, when, team) for s in w.services.values() for when, team in s.owners[1:]]
    for s, when, team in b.rng.sample(moved, 3):
        lead = w.lead_on(team, when)
        b.add(
            "asof-event-lead",
            "as_of",
            f"Who led {team} at the time {s.name} moved to that team?",
            [person_fact(lead, f"{lead} led {team}")],
            [*b.handover_hops(s.name, when), b.directory_hop(lead, when)],
            "the handover post names the lead; the charter names only today's",
        )
    for p in b.rng.sample([x for x in w.people.values() if len(x.moves) > 1], 3):
        when, team = p.moves[-1]
        head = w.head_on(when)
        lead = w.lead_on(team, when)
        b.add(
            "asof-event-join",
            "as_of",
            f"When {p.name} joined {team}, who was leading that team and who was Head of Port "
            f"Systems?",
            [person_fact(lead, f"{lead} led {team}"), person_fact(head, f"{head} was Head")],
            [
                b.c.hop(f"{p.name} joins {team}"),
                b.directory_hop(lead, when),
                b.directory_hop(head, when),
            ],
            "both people changed during the period",
        )


def asof_lead(b: Builder) -> None:
    w = b.w
    for team, history in list(w.leads.items()):
        if len(history) < 2 or b.seen["asof-lead"] >= 3:
            continue
        when, new = history[-1]
        on = when - timedelta(days=b.rng.randint(25, 70))
        old = history[0][1]
        snap = max(x for x in r.directory_dates() if x <= on)
        row = f"| {old} | Team Lead | {team} |"
        b.add(
            "asof-lead",
            "as_of",
            f"Who led {team} on {long_date(on)}?",
            [person_fact(old, f"{old} led it then")],
            [
                b.c.hop(row, r.directory_title(snap)),
                b.c.hop(f"{new} takes over as lead of {team} from {day_date(when)}"),
            ],
            "the charter names only the current lead",
        )


def asof_policy(b: Builder) -> None:
    versions = POLICIES["Travel and subsistence policy"]
    name = "Travel and subsistence policy"
    cases = [
        (date(2025, 2, 11), "Rotterdam", 170, 1, None),
        (date(2025, 9, 3), "London", 205, 2, None),
        (date(2026, 1, 20), "Rotterdam", 198, 2, r.ROTTERDAM_NOTICE),
    ]
    for on, city, cap, version, notice in cases:
        hops = [
            b.c.hop(
                f"| {city} | £{versions[version - 1][1][city]} |", r.policy_title(name, version)
            ),
            b.c.hop(
                f"Effective from {long_date(versions[version][0])}",
                r.policy_title(name, version + 1),
            ),
        ]
        if notice:
            hops.append(b.c.hop(f"goes up to £{notice[1]} a night"))
        b.add(
            "asof-travel",
            "as_of",
            f"What was the hotel cap per night in {city} on {long_date(on)}?",
            [fact(f"£{cap}", f"£{cap}", str(cap))],
            hops,
            "versions carry an effective date; only version 1 says it's superseded"
            + ("; an interim change was made in Slack" if notice else ""),
        )


def asof_schedules(b: Builder) -> None:
    cases = [
        ("How long were AIS vessel tracks kept in August 2025?", RETENTION, 0, 1,
         lambda row: f"{row[1]} days", "Data retention schedule"),
        ("How long was CCTV footage kept on 10 May 2026?", RETENTION, 1, 2,
         lambda row: f"{row[2]} days", "Data retention schedule"),
        ("How many days before quarter end did the change freeze on tier 1 services start in "
         "June 2025?", FREEZE, 0, 1, lambda row: f"{row[1]} calendar days", "Change freeze policy"),
        ("Who could waive the change freeze in December 2025?", FREEZE, 1, 2,
         lambda row: row[2], "Change freeze policy"),
    ]  # fmt: skip
    for question, rows, index, version, value, name in cases:
        answer = value(rows[index])
        title = r.policy_title(name, version)
        quote = answer.replace(" calendar", "") if name.startswith("Data") else answer
        b.add(
            "asof-schedule",
            "as_of",
            question,
            [fact(answer, answer, answer.replace("calendar days", "days"))],
            [
                b.c.hop(quote, title),
                b.c.hop(
                    f"Effective from {long_date(rows[index + 1][0])}",
                    r.policy_title(name, version + 1),
                ),
            ],
            f"version {version} was in force; a later version says otherwise",
        )


def asof_person(b: Builder) -> None:
    w = b.w
    movers = [p for p in w.people.values() if len(p.moves) > 1]
    for p in b.rng.sample(movers, 3):
        when, new = p.moves[-1]
        old = p.moves[0][1]
        on = when - timedelta(days=b.rng.randint(15, 45))
        b.add(
            "asof-person",
            "as_of",
            f"Which team was {p.name} in on {long_date(on)}?",
            [fact(old, old)],
            [b.directory_hop(p.name, on)],
            f"they joined {new} from {when}",
        )


def asof_action(b: Builder) -> None:
    found = 0
    for i in b.major():
        for a in i.actions:
            if a.closed and (a.closed - i.day).days > 25 and found < 3:
                on = a.closed - timedelta(days=b.rng.randint(5, 12))
                if on <= i.day:
                    continue
                b.add(
                    "asof-action",
                    "as_of",
                    f"As of {long_date(on)}, had the action to {a.text[0].lower()}{a.text[1:]} "
                    f"from {i.id} been finished? If not, when was it reported done?",
                    [
                        fact("Not yet", "not yet", "no", "open", "not finished", "still"),
                        fact(
                            f"It was reported done on {long_date(a.closed)}", *date_forms(a.closed)
                        ),
                    ],
                    [
                        b.action_hop(i, a),
                        b.c.hop(f'{i.id}: done. "{a.text}"'),
                    ],
                    "the question is about a date before the 'done' post",
                    derivation={
                        "op": "before",
                        "asked": on.isoformat(),
                        "reported": a.closed.isoformat(),
                        "result": "Not yet",
                        "proves": "Not yet",
                    },
                )
                found += 1


def asof_rto(b: Builder) -> None:
    w = b.w
    changed = [
        s
        for s in w.services.values()
        if len(s.rto_hours) > 1 and s.page_reviewed < s.rto_hours[-1][0]
    ]
    for s in changed[:3]:
        when, new = s.rto_hours[-1]
        old = s.rto_hours[0][1]
        on = when + timedelta(days=b.rng.randint(20, 40))
        new_or_old = s.rto_on(on)
        b.add(
            "asof-rto",
            "as_of",
            f"What was the recovery time target for {s.name} in {month_name(on)}?",
            [
                fact(
                    f"{new_or_old} hours",
                    f"{new_or_old} hours",
                    f"{new_or_old}-hour",
                    f"{new_or_old} hour",
                )
            ],
            b.posted_hops(("rto", s.name, when), s.name),
            f"the runbook says {old}; the change came in a Slack post",
        )


# ── which source wins: the newest statement beats the page ───────────────────
def latest_owner(b: Builder) -> None:
    w = b.w
    stale = [
        s for s in w.services.values() if len(s.owners) > 1 and s.page_reviewed < s.owners[-1][0]
    ]
    stale.sort(key=lambda s: -len(s.owners))
    for s in stale[:6]:
        when, new = s.owners[-1]
        b.add(
            "latest-owner",
            "superseded",
            f"Which team owns {s.name}?",
            [fact(new, new)],
            [*b.handover_hops(s.name, when), b.c.hop(f"- {s.name}", r.team_title(new))],
            f"the page (reviewed {s.page_reviewed}) still says {s.owner_on(s.page_reviewed)}",
        )


def latest_called_off(b: Builder) -> None:
    for s in [x for x in b.w.services.values() if x.reversal]:
        _, team, _ = s.reversal
        owner = s.owner_on(END)
        b.add(
            "latest-called-off",
            "superseded",
            f"Did {s.name} ever move to {team}, and which team runs it now?",
            [
                fact(
                    f"It did not move to {team}",
                    "called off",
                    "stays with",
                    "stayed",
                    "didn't move",
                    "did not move",
                    "never moved",
                    "not moved",
                    "no",
                    "change of plan",
                ),
                fact(owner, owner),
            ],
            [
                *b.posted_hops(("planned", s.name), s.name),
                b.c.hop(b.w.posted[("called-off", s.name)]),
                b.c.hop(f"- {s.name}", r.team_title(owner)),
            ],
            "an announcement and, days later, a retraction",
        )


def latest_policy(b: Builder) -> None:
    version, _ = RETENTION_WITHDRAWN
    in_force = RETENTION[version - 2]
    title = r.policy_title("Data retention schedule", version - 1)
    cases = [
        ("How long do we keep CCTV footage?", "CCTV footage", in_force[2]),
        ("How long are call recordings kept?", "Call recordings", in_force[3]),
        ("What's the retention period for AIS vessel tracks?", "AIS vessel tracks", in_force[1]),
    ]
    for question, label, days in cases:
        b.add(
            "latest-policy",
            "superseded",
            question,
            [fact(f"{days} days", f"{days} days", f"{days}-day")],
            [
                b.c.hop(f"| {label} | {days} days |", title),
                b.c.hop(f"Version {version} of the data retention schedule is withdrawn"),
            ],
            f"version {version} is the newest document but was withdrawn before taking effect",
        )


def latest_rto(b: Builder) -> None:
    w = b.w
    stale = [
        s
        for s in w.services.values()
        if len(s.rto_hours) > 1 and s.page_reviewed < s.rto_hours[-1][0]
    ]
    for s in stale[:3]:
        when, hours = s.rto_hours[-1]
        b.add(
            "latest-rto",
            "superseded",
            f"What's the recovery time target for {s.name}?",
            [fact(f"{hours} hours", f"{hours} hours", f"{hours}-hour", f"{hours} hour")],
            b.posted_hops(("rto", s.name, when), s.name),
            f"the runbook still says {s.rto_on(s.page_reviewed)} hours",
        )


def latest_renewal(b: Builder) -> None:
    varied = [v for v in b.w.vendors.values() if v.variation]
    for v in varied[:7]:
        signed, renewal, notice = v.variation
        facts = [fact(long_date(renewal), *date_forms(renewal))]
        hops = b.renewal_hops(v)
        ask = f"When does our contract with {v.name} renew"
        if notice:
            facts.append(fact(f"{notice} days' notice", f"{notice} days", f"{notice}-day"))
            hops.append(b.c.hop(f"notice period becomes {notice} days", r.variation_title(v.name)))
            ask += ", and how much notice do we have to give"
        extra = (
            {
                "derivation": {
                    "op": "add_months",
                    "from": long_date(v.renewal),
                    "months": 12,
                    "result": long_date(renewal),
                    "proves": long_date(renewal),
                }
            }
            if v.relative
            else {}
        )
        b.add(
            "latest-renewal",
            "superseded",
            ask + "?",
            facts,
            hops,
            "the vendor page and the January register show the old terms",
            **extra,
        )


def latest_impact(b: Builder) -> None:
    for i in [x for x in b.major() if x.corrected_impact is not None][:3]:
        b.add(
            "latest-impact",
            "superseded",
            f"What was the impact of {i.id}?",
            [fact(f"{i.corrected_impact} {i.impact_label}", str(i.corrected_impact))],
            [b.c.hop(f"should be {i.corrected_impact} {i.impact_label}, not {i.impact}")],
            "the postmortem wasn't reissued",
        )


def latest_availability(b: Builder) -> None:
    w = b.w
    for m, name, value, _ in w.corrections[:3]:
        b.add(
            "latest-availability",
            "superseded",
            f"What was {name}'s availability in {month_name(m)}?",
            [fact(f"{value:.2f}%", f"{value:.2f}")],
            b.posted_hops(("availability", m, name), name),
            "the monthly report wasn't amended",
        )


def latest_decision(b: Builder) -> None:
    w = b.w
    ends = [d for d in w.decisions.values() if not d.superseded_by]
    chains = [d for d in ends if any(o.superseded_by == d.id for o in w.decisions.values())]
    for d in b.rng.sample(chains, min(3, len(chains))):
        topic = d.title.split(" for the ", 1)[1]
        b.add(
            "latest-decision",
            "superseded",
            f"What do we use for the {topic}, and which decision record chose it?",
            [fact(d.choice, d.choice), fact(d.id, d.id)],
            [b.c.hop(f"We chose {d.choice}", r.decision_title(d))],
            "earlier records aren't marked as replaced",
        )


# ── look-alikes: the right page among many that read the same ────────────────
def dup_runbook(b: Builder) -> None:
    w = b.w
    pairs = [
        ("Berth Allocator", "Berth Allocation API"),
        ("Lock Scheduler", "Lock Gate Telemetry"),
        ("Tide Gauge Ingest", "Tide Tables API"),
    ]
    for one, two in pairs:
        a, c = w.services[one], w.services[two]
        b.add(
            "dup-runbook",
            "near_duplicate",
            f"What are the connection pool sizes and health check ports of {one} and {two}?",
            [
                fact(f"{one} pool {a.pool_size}", str(a.pool_size)),
                fact(f"{one} port {a.port}", str(a.port)),
                fact(f"{two} pool {c.pool_size}", str(c.pool_size)),
                fact(f"{two} port {c.port}", str(c.port)),
            ],
            [
                b.c.hop(f"Connection pool: {a.pool_size}", r.runbook_title(one)),
                b.c.hop(f"Health check port: {a.port}", r.runbook_title(one)),
                b.c.hop(f"Connection pool: {c.pool_size}", r.runbook_title(two)),
                b.c.hop(f"Health check port: {c.port}", r.runbook_title(two)),
            ],
            "two pages from one template, with nearly the same name",
        )


def dup_report(b: Builder) -> None:
    w = b.w
    corrected = {(m, n) for m, n, _, _ in w.corrections}
    names = ["Berth Allocator", "Tide Tables API", "Pilot Booking"]
    for name in names:
        m = b.rng.choice([x for x in months(date(2025, 3, 1)) if (x, name) not in corrected])
        s = w.services[name]
        value = w.availability[(m, name)]
        b.add(
            "dup-report",
            "near_duplicate",
            f"What availability did {name} report for {month_name(m)}, and did it meet its target?",
            [
                fact(f"{value:.2f}%", f"{value:.2f}"),
                fact(
                    "Met the target" if value >= SLO[s.tier] else "Missed the target",
                    *(
                        ["met", "above", "yes"]
                        if value >= SLO[s.tier]
                        else ["miss", "below", "short", "no"]
                    ),
                ),
            ],
            [b.c.hop(f"| {name} | {s.tier} | {SLO[s.tier]}% | {value:.2f}% |", r.report_title(m))],
            "22 monthly reports of 24 near-identical rows",
        )


def dup_rota(b: Builder) -> None:
    w = b.w
    swapped = {wk for wk, *_ in w.swaps}
    for week in b.rng.sample(sorted(set(w.rota) - swapped), 3):
        sec = w.rota[week][1]
        b.add(
            "dup-rota",
            "near_duplicate",
            f"Who was secondary on call in the week commencing {long_date(week)}, and which team "
            f"were they in?",
            [person_fact(sec), fact(w.people[sec].team_on(week), w.people[sec].team_on(week))],
            [b.rota_hop(week), b.directory_hop(sec, week)],
            "rota documents differ only in their dates; they use initials",
        )


def dup_incident(b: Builder) -> None:
    per_service = Counter(i.service for i in b.major())
    busy = [s for s, n in per_service.items() if n >= 3]
    picked = 0
    for name in busy:
        rows = [i for i in b.major() if i.service == name]
        i = rows[len(rows) // 2]
        if not b.unique_handle(i) or picked >= 3:
            continue
        b.add(
            "dup-incident",
            "near_duplicate",
            f"How long did the {i.service} incident caused by {i.cause_short} in "
            f"{month_name(i.day)} last, and who handled comms?",
            [
                fact(f"{i.minutes} minutes", f"{i.minutes} minutes", f"{i.minutes} min"),
                person_fact(i.comms),
            ],
            [
                b.c.hop(f"{i.minutes} minutes", r.postmortem_title(i)),
                b.c.hop(i.comms, r.postmortem_title(i)),
            ],
            f"{i.service} has {len(rows)} postmortems",
        )
        picked += 1


def dup_namesake(b: Builder) -> None:
    w = b.w
    pairs = [
        ("Marcus Bell", "Marcus Feld", "Platform"),
        ("Priya Menon", "Priya Raman", "Leadership"),
        ("Sam Whitlock", "Sam Doyle", "Book a Mooring"),
    ]
    for ours, theirs, their_team in pairs:
        if ours not in w.people:
            continue
        team = w.people[ours].team_on(END)
        b.add(
            "dup-namesake",
            "near_duplicate",
            f"I keep mixing up {ours} and {theirs}. Which team is each of them in?",
            [fact(f"{ours} is in {team}", team), fact(f"{theirs} is in {their_team}", their_team)],
            [
                b.directory_hop(ours, END),
                {"doc": "Team directory", "quote": f"| {theirs} |"},
            ],
            f"{theirs} is in the Harbourline Digital directory (sample data)",
        )


# ── who is asking ────────────────────────────────────────────────────────────
def permissions(b: Builder) -> None:
    w = b.w
    # The budget: an exact, recent figure for finance; an old round one for everyone.
    day, capital, _ = w.budgets[-1]
    d, figure = w.public_budget
    exact = f"£{capital:,}k"
    q = "What's the full-year capital forecast for Port Systems?"
    title = f"Port Systems all-hands: {month_name(d)}"
    b.add(
        "perm-budget", "permissions", q,
        [fact(exact, exact, f"{capital:,}", f"£{capital / 1000:.2f}m", f"£{capital / 1000:.1f}m")],
        [b.c.hop(f"| Capital | {exact} |", r.forecast_title(day))],
        "finance sees the forecasts", asker=FINANCE,
    )  # fmt: skip
    b.add(
        "perm-budget", "permissions", q,
        [fact(f"The April all-hands figure: {figure}", figure.removeprefix("about "))],
        [b.c.hop(f"Capital spending this year is {figure}", title)],
        "staff see only the April all-hands figure", asker=STAFF,
        forbidden=[exact, f"{capital:,}"],
    )  # fmt: skip
    # Contract values: finance only.
    for name in b.rng.sample(sorted(w.vendors), 2):
        v = w.vendors[name]
        q = f"How much do we pay {name} a year?"
        b.add(
            "perm-contract", "permissions", q,
            [fact(f"£{v.annual_value:,}", *money_forms(v.annual_value))],
            [{"doc": CONTRACTS, "quote": f"**Annual value (GBP)**: {v.annual_value}"}],
            "finance sees contract values", asker=FINANCE,
        )  # fmt: skip
        b.add(
            "perm-contract", "permissions", q, [], [],
            "staff can see the vendor but not its price: they should say so", asker=STAFF,
            forbidden=[f"£{v.annual_value:,}", str(v.annual_value)],
        )  # fmt: skip
    # Launch dates: the leads know about a slip that staff don't.
    for name, (public, newer) in list(w.go_lives.items())[:2]:
        q = f"When does {name} 2.0 go live?"
        b.add(
            "perm-golive", "permissions", q,
            [fact(long_date(newer), *date_forms(newer))],
            [b.c.hop(f"{name} 2.0 go-live moves from {long_date(public)} to {long_date(newer)}")],
            "the leads meeting moved it; the announcement stands for everyone else", asker=LEADS,
        )  # fmt: skip
        b.add(
            "perm-golive", "permissions", q,
            [fact(long_date(public), *date_forms(public))],
            [b.c.hop(f"{name} 2.0 goes live on {day_date(public)}")],
            "staff should get the announced date and no hint of the slip", asker=STAFF,
            forbidden=[long_date(newer)],
        )  # fmt: skip
    # The merger.
    x = w.reorg
    q = f"Is {x['merged']} being merged into another team?"
    eff = x["effective"]
    b.add(
        "perm-reorg", "permissions", q,
        [fact(f"Into {x['into']}", x["into"]), fact(long_date(eff), *date_forms(eff))],
        [b.c.hop(f"merge {x['merged']} into {x['into']} from {long_date(eff)}")],
        "confidential to the leads", asker=LEADS,
    )  # fmt: skip
    b.add(
        "perm-reorg", "permissions", q, [], [],
        "nothing about this is visible to staff", asker=STAFF,
        forbidden=[long_date(eff), f"into {x['into']}"],
    )  # fmt: skip
    # Pay.
    for role in ("Senior Developer",):
        lo, hi = w.pay_bands[role]
        q = f"What's the pay band for a {role} in Port Systems?"
        b.add(
            "perm-pay", "permissions", q,
            [fact(f"£{lo:,}", *money_forms(lo)), fact(f"£{hi:,}", *money_forms(hi))],
            [b.c.hop(f"| {role} | £{lo:,} | £{hi:,} |", "Port Systems pay bands 2026")],
            "people partners only", asker=PEOPLE,
        )  # fmt: skip
        b.add(
            "perm-pay", "permissions", q, [], [],
            "staff can't see pay bands", asker=STAFF,
            forbidden=[f"{lo:,}", f"{hi:,}"],
        )  # fmt: skip
    # Security findings.
    for p in [x for x in w.pen_tests if x["critical"] > 0][:1]:
        q = (
            f"Did the 2026 penetration test of {p['service']} find anything critical, and what "
            f"was the most serious finding?"
        )
        b.add(
            "perm-security", "permissions", q,
            [fact(f"{p['critical']} critical", f"{p['critical']} critical",
                  *number_forms(p["critical"])),
             fact(p["finding"], p["finding"])],
            [b.c.hop(p["finding"], r.pen_test_title(p))],
            "the security team sees the report", asker=SECURITY,
        )  # fmt: skip


# ── questions with no answer here, next to ones that have ────────────────────
def unanswerable(b: Builder) -> None:
    w = b.w
    no_rpo = [s for s in w.services.values() if s.rpo_minutes is None]
    for s in no_rpo[:3]:
        b.add(
            "none-rpo", "unanswerable", f"What's the recovery point target for {s.name}?", [], [],
            "its runbook has no recovery point; its siblings' do",
            absent_from={r.runbook_title(s.name): "Recovery point"},
        )  # fmt: skip
    last = max(i.id for i in w.incidents if i.day.year == 2026)
    n = int(last.rsplit("-", 1)[1])
    for k, ask in (
        (3, "What caused {id}, and who led the response?"),
        (7, "Who led the postmortem for {id}?"),
    ):
        b.add(
            "none-incident", "unanswerable", ask.format(id=f"PS-2026-{n + k:03d}"), [], [],
            "the numbering stops before this reference", absent=[f"PS-2026-{n + k:03d}"],
        )  # fmt: skip
    minors = [i for i in w.incidents if not i.has_postmortem][:3]
    for i in minors:
        b.add(
            "none-comms", "unanswerable", f"Who handled comms for {i.id}?", [], [],
            "a severity 3 note names no comms lead; every postmortem does",
            absent=[f"{i.id}: {i.service} is having a bad time"],
        )  # fmt: skip
    b.add(
        "none-vendor", "unanswerable",
        "What's the notice period on our Driftwood Analytics contract?",
        [], [], "Anchorage Analytics exists; Driftwood doesn't", absent=["Driftwood Analytics"],
    )  # fmt: skip
    b.add(
        "none-vendor", "unanswerable", "When does the Harbourside Telemetry contract renew?",
        [], [], "Northlight Telemetry exists; Harbourside doesn't",
        absent=["Harbourside Telemetry"],
    )  # fmt: skip
    b.add(
        "none-policy", "unanswerable", "What's the hotel cap in Hamburg under the travel policy?",
        [], [], "the policy lists three cities", absent=["Hamburg"],
    )  # fmt: skip
    b.add(
        "none-policy", "unanswerable", "What's the mileage rate in the travel policy?",
        [], [], "the policy covers hotels and meals only",
        absent_from={r.policy_title("Travel and subsistence policy", 3): "mileage"},
    )  # fmt: skip
    b.add(
        "none-decision", "unanswerable", "Why did we reject Nomad for container orchestration?",
        [], [], "Nomad was never considered", absent=["Nomad"],
    )  # fmt: skip
    b.add(
        "none-decision", "unanswerable", "What did TDR-099 decide?",
        [], [], "the numbering stops earlier", absent=["TDR-099"],
    )  # fmt: skip


FAMILIES = [
    chain_oncall, chain_handover, chain_escalation, chain_handover_oncall, chain_action,
    chain_decision, chain_standby, chain_lead_pay, chain_dependency,
    agg_count, agg_owner, agg_impact, agg_open_actions, agg_slo, agg_renewals, agg_cause, agg_lead,
    asof_owner, asof_announced, asof_event, asof_lead, asof_policy, asof_schedules, asof_person,
    asof_action, asof_rto,
    latest_owner, latest_called_off, latest_policy, latest_rto, latest_renewal, latest_impact,
    latest_availability, latest_decision,
    dup_runbook, dup_report, dup_rota, dup_incident, dup_namesake,
    permissions, unanswerable,
]  # fmt: skip


def generate() -> tuple[World, list[Doc], list[dict]]:
    w = build()
    docs = r.render(w)
    b = Builder(w, docs)
    for family in FAMILIES:
        family(b)
    return w, docs, b.rows


def main() -> None:
    _, docs, rows = generate()
    real = HERE / "questions_real.jsonl"
    rows += (
        [json.loads(x) for x in real.read_text().splitlines() if x.strip()] if real.exists() else []
    )
    for split, name in (("dev", "questions_dev.jsonl"), ("test", "questions.jsonl")):
        chosen = [q for q in rows if q["split"] == split]
        (HERE / name).write_text(
            "\n".join(json.dumps(q, ensure_ascii=False) for q in chosen) + "\n"
        )
        print(name, len(chosen), dict(Counter(q["category"] for q in chosen)))
    print(f"{len(docs)} documents")


if __name__ == "__main__":
    main()
