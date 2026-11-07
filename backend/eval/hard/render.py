"""The documents of the hard set's world (see world.py for why they look like this).

Each function writes one kind of document from the truth in `World`. What a document
says is what it would say in real life, which isn't always the truth: `Doc.text` for a
service page reports its last review, not today. Titles are unique within the world.
"""

import csv
import io
from collections import defaultdict
from datetime import date, timedelta

from eval.hard.world import (
    END,
    FREEZE,
    POLICIES,
    RETENTION,
    RETENTION_WITHDRAWN,
    ROLES,
    SERVICES,
    SLO,
    STANDBY,
    START,
    TEAMS,
    Decision,
    Doc,
    Incident,
    Person,
    Service,
    World,
    day_date,
    long_date,
    month_name,
    months,
    short_date,
    slug,
)

ENGINEERING = "Port Systems Engineering"
OPERATIONS = "Port Systems Operations"
POLICY = "Port Systems Policies"
SLACK = "Port Systems Slack"
LEADERSHIP = "Port Systems Leadership"
FINANCE = "Port Systems Finance"
PEOPLE = "Port Systems People"
SECURITY = "Port Systems Security"
RESTRICTED = [LEADERSHIP, FINANCE, PEOPLE, SECURITY]

ROTTERDAM_NOTICE = (date(2025, 11, 17), 198)  # an amendment between two policy versions

FILLER = [
    "Reminder that the fire drill is at 11:00 on Thursday.",
    "Who's got the spare AIS test receiver? It isn't in the cupboard.",
    "Lunch and learn next week is on writing better alerts.",
    "The Quay Offices lift is out again, use the stairs by reception.",
    "Please fill in the access review spreadsheet by Friday.",
    "Thanks all for the show and tell, the recording is in the usual place.",
    "Staging will be slow this afternoon while we refresh the data.",
    "Has anyone renewed the shared calendar for the harbour master's office?",
    "Desk booking is down for maintenance tonight.",
    "New starters: the induction slides were updated this week.",
]


def table(header: list[str], rows: list[list[str]]) -> str:
    out = [f"| {' | '.join(header)} |", f"| {' | '.join('---' for _ in header)} |"]
    out += [f"| {' | '.join(str(c) for c in r)} |" for r in rows]
    return "\n".join(out)


# ── directory, teams ─────────────────────────────────────────────────────────
def role_on(w: World, p: Person, d: date) -> str:
    if p.role in ("Head of Port Systems", "Service Manager"):
        if p.name == w.head_on(d):
            return "Head of Port Systems"
        return "Service Manager" if p.name in dict(w.head).values() else p.role
    team = p.team_on(d)
    if team in w.leads and w.lead_on(team, d) == p.name:
        return "Team Lead"
    was_lead = any(p.name == who and when <= d for t in w.leads.values() for when, who in t)
    if was_lead:
        return "Principal Engineer"
    return p.role if p.role in ROLES else "Senior Developer"


def directory_dates() -> list[date]:
    return [date(y, m, 1) for y in (2025, 2026) for m in (1, 4, 7, 10)]


def directory_title(d: date) -> str:
    return f"Port Systems staff directory: {d:%B %Y}"


def directory(w: World, d: date) -> Doc:
    rows = [
        [p.name, role_on(w, p, d), p.team_on(d), p.base]
        for p in sorted(w.people.values(), key=lambda p: p.name)
    ]
    text = (
        f"# {directory_title(d)}\n\nSnapshot taken on {long_date(d)}. Moves since then are "
        f"announced in #ps-announcements.\n\n" + table(["Name", "Role", "Team", "Based in"], rows)
    )
    return Doc(OPERATIONS, f"staff-directory-{d:%Y-%m}.md", directory_title(d), text)


def team_title(team: str) -> str:
    return f"Team charter: {team}"


def team_page(w: World, team: str) -> Doc:
    run = [s.name for s in w.services.values() if s.owner_on(END) == team]
    text = (
        f"# {team_title(team)}\n\nTeam lead: {w.lead_on(team, END)}\n\n"
        f"{team} is part of Port Systems. We keep the services below running and answer for "
        f"them out of hours through the Port Systems on-call rota.\n\n### Services we run\n\n"
        + "\n".join(f"- {n}" for n in run)
    )
    return Doc(OPERATIONS, f"team-{slug(team)}.md", team_title(team), text)


# ── services ─────────────────────────────────────────────────────────────────
def service_title(name: str) -> str:
    return f"Service: {name}"


def runbook_title(name: str) -> str:
    return f"Runbook: {name}"


def service_page(s: Service) -> Doc:
    d = s.page_reviewed
    lines = [
        f"# {service_title(s.name)}",
        "",
        f"Also known as: {s.codename}",
        f"Owner: {s.owner_on(d)}",
        f"Tier: {s.tier}",
        f"Availability target: {SLO[s.tier]}%",
        f"Region: {s.region}",
        f"Database: {s.database}",
        f"Key vendor: {s.vendor}",
        f"Depends on: {', '.join(s.depends_on)}",
        f"Last reviewed: {long_date(d)}",
    ]
    return Doc(ENGINEERING, f"service-{s.slug}.md", service_title(s.name), "\n".join(lines))


def runbook(s: Service) -> Doc:
    d = s.page_reviewed
    rpo = f"Recovery point target: {s.rpo_minutes} minutes\n" if s.rpo_minutes else ""
    text = (
        f"# {runbook_title(s.name)}\n\nReviewed {long_date(d)}.\n\n"
        f"Recovery time target: {s.rto_on(d)} hours\n{rpo}"
        f"Connection pool: {s.pool_size}\nHealth check port: {s.port}\n\n"
        f"### If {s.name} is down\n\n"
        f"1. Check the health endpoint on port {s.port} from the bastion.\n"
        f"2. Look at the last deploy and roll back if it was in the past hour.\n"
        f"3. Check {s.database} for long-running transactions and connection count.\n"
        f"4. If the primary is unhealthy, follow the failover steps for {s.region}.\n"
        f"5. Page the owning team's lead, then the Service Manager for tier {s.tier}.\n\n"
        f"### Dependencies\n\n" + "\n".join(f"- {n}" for n in s.depends_on)
    )
    return Doc(ENGINEERING, f"runbook-{s.slug}.md", runbook_title(s.name), text)


# ── incidents ────────────────────────────────────────────────────────────────
def postmortem_title(i: Incident) -> str:
    if i.style == "prose":
        return f"{i.service} outage on {long_date(i.day)}"
    return f"Postmortem {i.id}: {i.service}"


def postmortem(w: World, i: Incident) -> Doc:
    actions = i.actions
    title = postmortem_title(i)
    if i.style == "prose":
        joined = " ".join(
            f"{a.owner} will {a.text[0].lower() + a.text[1:]} by {long_date(a.due)}."
            for a in actions
        )
        text = (
            f"# {title}\n\nReference {i.id}. This was a severity {i.severity} incident on "
            f"{day_date(i.day)}. {i.lead} led the response and {i.comms} handled "
            f"communications.\n\nAt {i.start} alerts fired for {i.service}. It turned out that "
            f"{i.cause_text}. Service was restored at {i.end}, {i.minutes} minutes later. The "
            f"impact was {i.impact} {i.impact_label}.\n\nWhat happens next: {joined}"
        )
    elif i.style == "lines":
        lines = [
            f"# {title}", "",
            f"Date: {long_date(i.day)}",
            f"Severity: {i.severity}",
            f"Service: {i.service}",
            f"Incident lead: {i.lead}",
            f"Comms: {i.comms}",
            f"Duration: {i.minutes} minutes ({i.start} to {i.end})",
            f"Impact: {i.impact} {i.impact_label}",
            "", "### Cause", "", f"{i.cause_text[0].upper()}{i.cause_text[1:]}.",
            "", "### Actions", "",
        ]  # fmt: skip
        lines += [f"- {a.text} ({a.owner}, due {short_date(a.due)})" for a in actions]
        text = "\n".join(lines)
    else:
        fields = [
            ["Date", long_date(i.day)], ["Severity", i.severity], ["Service", i.service],
            ["Incident lead", i.lead], ["Comms", i.comms],
            ["Duration", f"{i.minutes} minutes"], ["Impact", f"{i.impact} {i.impact_label}"],
        ]  # fmt: skip
        text = (
            f"# {title}\n\n" + table(["Field", "Value"], fields)
            + f"\n\n### Cause\n\n{i.cause_text[0].upper()}{i.cause_text[1:]}.\n\n### Actions\n\n"
            + table(
                ["Action", "Owner", "Due"], [[a.text, a.owner, short_date(a.due)] for a in actions]
            )
        )  # fmt: skip
    return Doc(ENGINEERING, f"postmortem-{i.id.lower()}.md", title, text)


# ── rota ─────────────────────────────────────────────────────────────────────
def rota_title(m: date) -> str:
    return f"Port Systems on-call rota: {month_name(m)}"


def rota_doc(w: World, m: date) -> Doc:
    rows = []
    for week, (primary, secondary) in sorted(w.rota.items()):
        if (week.year, week.month) == (m.year, m.month):
            rows.append([long_date(week), w.people[primary].initial, w.people[secondary].initial])
    text = (
        f"# {rota_title(m)}\n\nWeeks run Monday 09:00 to Monday 09:00. This is the rota as "
        f"published at the start of the month; swaps are agreed in #ps-oncall.\n\n"
        + table(["Week commencing", "Primary", "Secondary"], rows)
    )
    return Doc(OPERATIONS, f"rota-{m:%Y-%m}.md", rota_title(m), text)


# ── service reports ──────────────────────────────────────────────────────────
def report_title(m: date) -> str:
    return f"Port Systems service report: {month_name(m)}"


def report(w: World, m: date) -> Doc:
    rows = []
    for name, _, tier in SERVICES:
        rows.append([name, tier, f"{SLO[tier]}%", f"{w.availability[(m, name)]:.2f}%"])
    majors = [
        i for i in w.incidents if (i.day.year, i.day.month) == (m.year, m.month) and i.severity <= 2
    ]
    notes = "; ".join(f"{i.id} ({i.service})" for i in majors) or "none"
    text = f"# {report_title(m)}\n\nMajor incidents this month: {notes}.\n\n" + table(
        ["Service", "Tier", "Target", "Availability"], rows
    )
    return Doc(OPERATIONS, f"service-report-{m:%Y-%m}.md", report_title(m), text)


# ── policies ─────────────────────────────────────────────────────────────────
def policy_title(name: str, version: int) -> str:
    return f"{name} (version {version})"


def travel_policy(version: int, effective: date, caps: dict, meal: int, last: bool) -> Doc:
    name = "Travel and subsistence policy"
    banner = "" if version != 1 else "> Superseded. Kept for the record.\n\n"
    rows = [[city, f"£{cap}"] for city, cap in caps.items()]
    text = (
        f"# {policy_title(name, version)}\n\n{banner}Effective from {long_date(effective)}. "
        f"Applies to all Port Systems staff.\n\n### Hotel caps per night\n\n"
        + table(["City", "Cap"], rows)
        + f"\n\n### Subsistence\n\nMeals are reimbursed up to £{meal} a day with receipts."
    )
    return Doc(POLICY, f"travel-v{version}.md", policy_title(name, version), text)


def standby_policy(version: int, effective: date, weekly: int, callout: int) -> Doc:
    name = "Standby allowance policy"
    text = (
        f"# {policy_title(name, version)}\n\nEffective from {long_date(effective)}.\n\n"
        f"Port Systems staff on the on-call rota are paid a standby allowance of £{weekly} for "
        f"each week commencing in the month, primary or secondary alike, and £{callout} for "
        f"each call-out outside working hours. A swap moves the allowance with the week."
    )
    return Doc(POLICY, f"standby-v{version}.md", policy_title(name, version), text)


def retention_policy(version: int, effective: date, ais: int, cctv: int, calls: int) -> Doc:
    name = "Data retention schedule"
    rows = [
        ["AIS vessel tracks", f"{ais} days"],
        ["CCTV footage", f"{cctv} days"],
        ["Call recordings", f"{calls} days"],
    ]
    text = f"# {policy_title(name, version)}\n\nEffective from {long_date(effective)}.\n\n" + table(
        ["Record", "Kept for"], rows
    )
    return Doc(POLICY, f"retention-v{version}.md", policy_title(name, version), text)


def freeze_policy(version: int, effective: date, days: int, waiver: str) -> Doc:
    name = "Change freeze policy"
    text = (
        f"# {policy_title(name, version)}\n\nEffective from {long_date(effective)}.\n\n"
        f"Production changes to tier 1 services stop {days} calendar days before each quarter "
        f"end. Only {waiver} can waive the freeze."
    )
    return Doc(POLICY, f"freeze-v{version}.md", policy_title(name, version), text)


# ── vendors ──────────────────────────────────────────────────────────────────
def vendor_title(name: str) -> str:
    return f"Vendor: {name}"


def vendor_page(v) -> Doc:
    fields = [
        ["Provides", v.provides],
        ["Team contact", v.owner],
        ["Risk rating", v.risk],
        ["Renewal date", long_date(v.renewal)],
        ["Notice period", f"{v.notice_days} days"],
    ]
    text = f"# {vendor_title(v.name)}\n\n" + table(["Field", "Value"], fields)
    return Doc(OPERATIONS, f"vendor-{slug(v.name)}.md", vendor_title(v.name), text)


def variation_title(name: str) -> str:
    return f"Contract variation: {name}"


def variation_doc(v) -> Doc:
    signed, renewal, notice = v.variation
    extra = f" The notice period becomes {notice} days." if notice else ""
    moved = (
        "The term is extended by 12 months from the current renewal date."
        if v.relative
        else f"The renewal date moves from {long_date(v.renewal)} to {long_date(renewal)}."
    )
    text = (
        f"# {variation_title(v.name)}\n\nSigned on {long_date(signed)}.\n\n"
        f"This variation extends the agreement for {v.provides}. {moved}{extra} All other terms "
        f"are unchanged."
    )
    return Doc(OPERATIONS, f"variation-{slug(v.name)}.md", variation_title(v.name), text)


def vendor_register(w: World) -> Doc:
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["Vendor", "Provides", "Risk", "Renewal date", "Notice days"])
    for v in w.vendors.values():
        out.writerow([v.name, v.provides, v.risk, v.renewal.isoformat(), v.notice_days])
    return Doc(
        OPERATIONS,
        "vendor-register-january-2026.csv",
        "vendor register january 2026",
        buf.getvalue(),
    )


def contract_values(w: World) -> Doc:
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["Vendor", "Annual value (GBP)"])
    for v in w.vendors.values():
        out.writerow([v.name, v.annual_value])
    return Doc(FINANCE, "contract-values-2026.csv", "contract values 2026", buf.getvalue())


# ── decisions ────────────────────────────────────────────────────────────────
def decision_title(d: Decision) -> str:
    return f"{d.id}: {d.title}"


def decision_doc(w: World, d: Decision) -> Doc:
    # Superseded decisions are rarely edited: only the newer one says what it replaced.
    replaced = next((o for o in w.decisions.values() if o.superseded_by == d.id), None)
    head = f"Replaces {replaced.id}. " if replaced else ""
    text = (
        f"# {decision_title(d)}\n\nStatus: Accepted. Decided by {d.decider} on "
        f"{long_date(d.decided)}.\n\n{head}We chose {d.choice} because {d.reason}."
    )
    return Doc(ENGINEERING, f"{d.id.lower()}.md", decision_title(d), text)


# ── restricted ───────────────────────────────────────────────────────────────
def forecast_title(d: date) -> str:
    return f"Port Systems budget forecast: {month_name(d)}"


def forecast(d: date, capital: int, revenue: int) -> Doc:
    text = (
        f"# {forecast_title(d)}\n\nPrepared on {long_date(d)}. Confidential to finance "
        f"partners.\n\n"
        + table(
            ["Line", "Full-year forecast"],
            [["Capital", f"£{capital:,}k"], ["Revenue", f"£{revenue:,}k"]],
        )
    )
    return Doc(FINANCE, f"forecast-{d:%Y-%m}.md", forecast_title(d), text)


def pay_bands_doc(w: World) -> Doc:
    rows = [[r, f"£{lo:,}", f"£{hi:,}"] for r, (lo, hi) in w.pay_bands.items()]
    title = "Port Systems pay bands 2026"
    text = f"# {title}\n\nConfidential.\n\n" + table(["Role", "Minimum", "Maximum"], rows)
    return Doc(PEOPLE, "pay-bands-2026.md", title, text)


def pen_test_title(p: dict) -> str:
    return f"Penetration test report: {p['service']}"


def pen_test(p: dict) -> Doc:
    text = (
        f"# {pen_test_title(p)}\n\nTested by {p['tester']} from {long_date(p['date'])}. "
        f"Restricted to the security team.\n\nFindings: {p['critical']} critical, {p['high']} "
        f"high. The most serious was {p['finding']}."
    )
    return Doc(SECURITY, f"pentest-{slug(p['service'])}.md", pen_test_title(p), text)


def assurance_summary(w: World) -> Doc:
    title = "Security assurance summary 2026"
    lines = [f"- {p['service']}: {p['public']}." for p in w.pen_tests]
    text = (
        f"# {title}\n\nWhat we can share with everyone about this year's testing.\n\n"
        + "\n".join(lines)
    )
    return Doc(OPERATIONS, "security-assurance-summary-2026.md", title, text)


def all_hands(w: World) -> Doc:
    d, figure = w.public_budget
    title = f"Port Systems all-hands: {month_name(d)}"
    lines = [
        f"# {title}", "", f"Held on {long_date(d)}.", "",
        f"- Capital spending this year is {figure}.",
        "- Thanks to everyone who covered the Easter on-call weeks.",
        "- The Lock Booking Portal 2.0, Pilot Transfer Tracker 2.0 and Tide Alerts 2.0 launches "
        "stay on plan.",
    ]  # fmt: skip
    return Doc(OPERATIONS, f"all-hands-{d:%Y-%m}.md", title, "\n".join(lines))


def leads_title(d: date) -> str:
    return f"Port Systems leads meeting: {long_date(d)}"


def leads_meetings(w: World) -> list[Doc]:
    """Fortnightly notes. Most of each is routine; the useful line is easy to miss."""
    rng = w.rng
    routine_decisions = [
        "Keep the Thursday service review at 30 minutes.",
        "Move the capacity review to the first week of the quarter.",
        "Hold the access review in the same week as the Digital team.",
        "Publish the on-call retrospective in #ps-general.",
    ]
    routine_updates = [
        "Recruitment: two offers out, no start dates yet.",
        "Sprint goals met by most teams.",
        "No change to the risk register this fortnight.",
        "The harbour master's office asked for a tour of the control room.",
        "Service desk volumes back to normal after the storm.",
    ]
    specials: dict[date, list[str]] = defaultdict(list)

    def meeting_after(d: date) -> date:
        return d + timedelta(days=(3 - d.weekday()) % 7)

    for name, (public, newer) in w.go_lives.items():
        when = public - timedelta(days=rng.randint(15, 30))
        specials[_fortnight(when)].append(
            f"{name} 2.0 go-live moves from {long_date(public)} to {long_date(newer)}: the "
            f"supplier needs more time for load testing. Don't announce this yet."
        )
    specials[_fortnight(w.reorg["minuted"])].append(
        f"Agreed in principle to merge {w.reorg['merged']} into {w.reorg['into']} from "
        f"{long_date(w.reorg['effective'])}. Confidential until consultation starts."
    )
    docs = []
    d = date(2025, 1, 9)
    while d <= END:
        decided = [rng.choice(routine_decisions)] + specials.get(d, [])
        attendees = [w.head_on(d)] + [w.lead_on(t, d) for t in rng.sample(TEAMS, 5)]
        text = (
            f"# {leads_title(d)}\n\nAttendees: {', '.join(attendees)}.\n\n### Decisions\n\n"
            + "\n".join(f"- {x}" for x in decided)
            + "\n\n### Updates\n\n"
            + "\n".join(f"- {x}" for x in rng.sample(routine_updates, 3))
        )
        docs.append(Doc(LEADERSHIP, f"leads-{d.isoformat()}.md", leads_title(d), text))
        d += timedelta(days=14)
    return docs


def _fortnight(d: date) -> date:
    """The leads meeting on or before d (they run every second Thursday)."""
    first = date(2025, 1, 9)
    return first + timedelta(days=(d - first).days // 14 * 14)


# ── Slack ────────────────────────────────────────────────────────────────────
def slack_docs(w: World) -> list[Doc]:
    rng = w.rng
    posts: dict[tuple[str, date], list[tuple[str, str, str]]] = defaultdict(list)

    def post(channel: str, d: date, author: str, text: str, at: str | None = None) -> str:
        at = at or f"{rng.randint(9, 16):02d}:{rng.randint(0, 59):02d}"
        posts[(channel, d)].append((at, author, text))
        return at

    head = lambda d: w.head_on(d)  # noqa: E731

    def nickname(s) -> str:
        """People write a service's project name more often than its real one."""
        return s.codename if rng.random() < 0.75 else s.name

    def effective(when: date) -> str:
        """Nobody writes the year in a chat message about next month."""
        return rng.choice(["from the 1st", "on the 1st", f"from {day_date(when)}"])

    # Handovers, announced here and often nowhere else.
    for s in w.services.values():
        for when, team in s.owners[1:]:
            announced = when - timedelta(days=rng.randint(3, 12))
            old = s.owner_on(when - timedelta(days=1))
            text = (
                f"{nickname(s)} moves from {old} to {team} {effective(when)}. "
                f"{w.lead_on(team, when)} picks up the on-call handover."
            )
            post("ps-announcements", announced, head(announced), text)
            w.posted[("handover", s.name, when)] = text
        if s.reversal:
            announced, team, off = s.reversal
            old = s.owner_on(announced)
            name = nickname(s)
            text = f"{name} moves from {old} to {team} next month. Details to follow."
            post("ps-announcements", announced, head(announced), text)
            w.posted[("planned", s.name)] = text
            text = f"Change of plan: {name} stays with {old} after all. Ignore my earlier post."
            post("ps-announcements", off, head(off), text)
            w.posted[("called-off", s.name)] = text
        for when, hours in s.rto_hours[1:]:
            text = (
                f"Agreed with the Service Manager: the recovery time target for {nickname(s)} is "
                f"{hours} hours from today. The runbook will catch up when it's next reviewed."
            )
            post("ps-engineering", when, w.lead_on(s.owner_on(when), when), text)
            w.posted[("rto", s.name, when)] = text
    # People moves and changes of lead.
    for p in w.people.values():
        for when, team in p.moves[1:]:
            announced = when - timedelta(days=rng.randint(3, 10))
            post(
                "ps-announcements", announced, head(announced),
                f"{p.name} joins {team} from {day_date(when)}. Please update your contact lists.",
            )  # fmt: skip
    for team, history in w.leads.items():
        for when, who in history[1:]:
            announced = when - timedelta(days=rng.randint(3, 10))
            post(
                "ps-announcements", announced, head(announced),
                f"{who} takes over as lead of {team} from {day_date(when)}. "
                f"{history[0][1]} becomes a Principal Engineer in the same team.",
            )  # fmt: skip
    for when, who in w.head[1:]:
        post(
            "ps-announcements", when - timedelta(days=11), w.head[0][1],
            f"I'm stepping back on {day_date(when)} and {who} becomes Head of Port Systems.",
        )  # fmt: skip

    # Rota swaps.
    for week, slot, new, announced, old in w.swaps:
        asked = post(
            "ps-oncall", announced, old,
            f"Can anyone take my {slot} slot for the week commencing {long_date(week)}?",
        )  # fmt: skip
        reply = f"{int(asked[:2]) + 1:02d}:{asked[3:]}"
        post(
            "ps-oncall", announced, new, f"I'll take it. Rota is updated for {long_date(week)}.",
            at=reply,
        )  # fmt: skip

    # Incidents: the minor ones live only here; corrections and action updates too.
    for i in w.incidents:
        if not i.has_postmortem:
            post(
                "ps-incidents", i.day, i.lead,
                f"{i.id} ({i.service}): {i.cause_text}. Severity 3, {i.minutes} minutes, "
                f"{i.impact} {i.impact_label}. I led this one. No postmortem needed.",
            )  # fmt: skip
        else:
            post(
                "ps-incidents", i.day, i.lead,
                f"{i.id}: {i.service} is having a bad time. I'm leading, {i.comms} is on comms.",
            )  # fmt: skip
            for a in i.actions:
                if a.closed:
                    post(
                        "ps-incidents", a.closed, a.owner,
                        f"{i.id}: done. \"{a.text}\" is finished and live.",
                    )  # fmt: skip
                elif a.reassigned:
                    when, who = a.reassigned
                    post(
                        "ps-incidents", when, a.owner,
                        f"{i.id}: I can't get to \"{a.text}\". {who} has agreed to own it from "
                        f"today, same due date.",
                    )  # fmt: skip
        if i.corrected_impact is not None:
            post(
                "ps-incidents", i.corrected_on, i.lead,
                f"Correction for {i.id}: the impact figure should be {i.corrected_impact} "
                f"{i.impact_label}, not {i.impact}. The postmortem won't be reissued.",
            )  # fmt: skip

    # Corrections to service reports.
    for month, name, value, posted in w.corrections:
        text = (
            f"Correction to the {month_name(month)} service report: {nickname(w.services[name])} "
            f"was {value:.2f}%, not {w.availability[(month, name)]:.2f}%. The monitoring export "
            f"double counted a maintenance window."
        )
        post("ps-service-reports", posted, w.head_on(posted), text)
        w.posted[("availability", month, name)] = text

    # An interim change to a policy, made between two versions.
    when, cap = ROTTERDAM_NOTICE
    post(
        "ps-announcements", when, w.head_on(when),
        f"Interim change to the travel policy, effective today: the Rotterdam hotel cap goes up "
        f"to £{cap} a night until the next version of the policy.",
    )  # fmt: skip

    version, when = RETENTION_WITHDRAWN
    post(
        "ps-announcements", when, w.head_on(when),
        f"Version {version} of the data retention schedule is withdrawn: legal aren't happy "
        f"with it. Version {version - 1} stays in force until further notice.",
    )  # fmt: skip

    # Launch dates, as staff know them.
    for name, (public, _) in w.go_lives.items():
        when = public - timedelta(days=rng.randint(70, 90))
        post(
            "ps-announcements",
            when,
            w.head_on(when),
            f"{name} 2.0 goes live on {day_date(public)}.",
        )

    # Noise, on days that already have traffic and days that don't.
    channels = ["ps-general", "ps-engineering", "ps-announcements", "ps-incidents", "ps-oncall"]
    pool = sorted(w.people)
    for _ in range(140):
        d = START + timedelta(days=rng.randrange((END - START).days))
        post(rng.choice(channels), d, rng.choice(pool), rng.choice(FILLER))

    docs = []
    for (channel, d), rows in sorted(posts.items()):
        title = f"#{channel} — {d.isoformat()}"
        body = "\n\n".join(f"**{a}** ({t}): {x}" for t, a, x in sorted(rows))
        docs.append(
            Doc(SLACK, f"slack-{channel}-{d.isoformat()}.md", title, f"# {title}\n\n{body}")
        )
    return docs


def slack_title(channel: str, d: date) -> str:
    return f"#{channel} — {d.isoformat()}"


# ── everything ───────────────────────────────────────────────────────────────
def render(w: World) -> list[Doc]:
    docs: list[Doc] = []
    docs += [directory(w, d) for d in directory_dates()]
    docs += [team_page(w, t) for t in TEAMS]
    docs += [service_page(s) for s in w.services.values()]
    docs += [runbook(s) for s in w.services.values()]
    docs += [postmortem(w, i) for i in w.incidents if i.has_postmortem]
    docs += [rota_doc(w, m) for m in months()]
    docs += [report(w, m) for m in months()]
    for versions in POLICIES.values():
        for n, (effective, caps, meal) in enumerate(versions, 1):
            docs.append(travel_policy(n, effective, caps, meal, n == len(versions)))
    docs += [standby_policy(n, *row) for n, row in enumerate(STANDBY, 1)]
    docs += [retention_policy(n, *row) for n, row in enumerate(RETENTION, 1)]
    docs += [freeze_policy(n, *row) for n, row in enumerate(FREEZE, 1)]
    docs += [vendor_page(v) for v in w.vendors.values()]
    docs += [variation_doc(v) for v in w.vendors.values() if v.variation]
    docs += [vendor_register(w), contract_values(w)]
    docs += [decision_doc(w, d) for d in w.decisions.values()]
    docs += [forecast(*row) for row in w.budgets]
    docs += [pay_bands_doc(w), assurance_summary(w), all_hands(w)]
    docs += [pen_test(p) for p in w.pen_tests]
    docs += leads_meetings(w)
    docs += slack_docs(w)
    titles = [d.title for d in docs]
    assert len(set(titles)) == len(titles), "titles must be unique"
    return docs
