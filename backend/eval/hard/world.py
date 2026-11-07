"""The hard set's world: the truth, and the documents that report it.

Harbourline Digital, the sample org, is about 30 short documents: one search returns
all of it, so chains of four hops pass and nothing is ever hard to find. Real
organisations aren't like that. They have hundreds of look-alike documents (a page per
service, a postmortem per incident, minutes every fortnight), written in formats that
drift over the years, and pages that nobody updated after the Slack message that changed
them.

This module builds that for a sibling division, Port Systems, which lives in the same
org as the sample data and the real GOV.UK documents. Everything is seeded, so the same
seed gives the same documents. `World` holds what is true; `render` writes the documents,
which report the truth the way real documents do:

* a service page may be stale: the handover was announced in Slack and nobody edited it;
* postmortems change format twice, so structured records cover only some of them;
* a correction arrives weeks later, in a thread, and the report is never amended;
* policies have several versions, not all of them marked as superseded;
* rota swaps exist only in Slack;
* restricted collections hold newer or more exact figures than the public documents.

The questions (questions.py) read `World`, so a gold answer is computed, not transcribed.
"""

import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

SEED = 20261107
START = date(2025, 1, 6)  # a Monday; the first rota week
END = date(2026, 10, 26)  # the last rota week; nothing in this world is later


# ── dates ────────────────────────────────────────────────────────────────────
def long_date(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def day_date(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B %Y}"


def short_date(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def month_name(d: date) -> str:
    return f"{d:%B %Y}"


def date_forms(d: date) -> list[str]:
    """The ways an answer may write a date, for a fact's any_of."""
    suffix = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return [
        long_date(d),
        short_date(d),
        d.isoformat(),
        f"{d:%B} {d.day}, {d.year}",
        f"{d.day}{suffix} {d:%B %Y}",
        f"{d.day:02d}/{d.month:02d}/{d.year}",
    ]


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def months(start: date = START, end: date = END) -> list[date]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        cur = date(cur.year + cur.month // 12, cur.month % 12 + 1, 1)
    return out


# ── the cast ─────────────────────────────────────────────────────────────────
TEAMS = [
    "Vessel Traffic", "Pilotage", "Harbour Dues", "Port Security",
    "Lock Operations", "Marine Data", "Customer Contact", "Infrastructure",
]  # fmt: skip
PEOPLE = [
    "Anwen Griffiths", "Bashir Haddad", "Callum Reid", "Chidi Eze", "Dana Kowalczyk",
    "Declan Murray", "Eleni Markou", "Farah Siddiqui", "Fergus Lamb", "Gwen Tudor",
    "Hamid Rahimi", "Hannah Voss", "Ines Carvalho", "Isla Mackay", "Jakub Nowak",
    "Joao Pinto", "Kavita Bose", "Kwame Asante", "Lena Fischer", "Liam Corrigan",
    "Mairi Sinclair", "Marcus Bell", "Mei Tanaka", "Nadia Petrova", "Nikhil Rao",
    "Olu Bankole", "Oskar Lind", "Priya Menon", "Rachel Adeyemi", "Rhys Morgan",
    "Rosa Delgado", "Sam Whitlock", "Sana Qureshi", "Siobhan Keane", "Stefan Ilic",
    "Tara Nolan", "Tom Reddy", "Uma Krishnan", "Viktor Hale", "Wen Zhao",
    "Yusuf Demir", "Zofia Wrona",
]  # fmt: skip
ROLES = ["Developer", "Senior Developer", "Site Reliability Engineer", "Analyst", "Tester"]
BASES = ["Pier House", "Quay Offices", "Remote (Hull)", "Remote (Dundee)", "Remote (Swansea)"]

# Name, team that first owned it, tier. Look-alike names on purpose: they are what a
# real catalogue accumulates, and what makes one service's page rank for another's.
SERVICES = [
    ("Berth Planner", "Vessel Traffic", 1),
    ("Berth Allocator", "Harbour Dues", 2),
    ("Berth Allocation API", "Vessel Traffic", 2),
    ("Tide Gauge Ingest", "Marine Data", 1),
    ("Tide Tables API", "Marine Data", 2),
    ("Tide Alerts", "Customer Contact", 3),
    ("Lock Scheduler", "Lock Operations", 1),
    ("Lock Gate Telemetry", "Lock Operations", 1),
    ("Lock Booking Portal", "Customer Contact", 2),
    ("Pilot Roster", "Pilotage", 1),
    ("Pilot Booking", "Pilotage", 2),
    ("Pilot Transfer Tracker", "Pilotage", 3),
    ("Dues Calculator", "Harbour Dues", 2),
    ("Dues Invoicing", "Harbour Dues", 1),
    ("Dues Reconciliation", "Harbour Dues", 3),
    ("Vessel Tracker", "Vessel Traffic", 1),
    ("Vessel Arrival Board", "Customer Contact", 3),
    ("AIS Gateway", "Infrastructure", 1),
    ("Port Pass", "Port Security", 2),
    ("Gate Access Control", "Port Security", 1),
    ("CCTV Archive", "Port Security", 3),
    ("Notices to Mariners", "Marine Data", 2),
    ("Hazard Reports", "Vessel Traffic", 3),
    ("Dredging Survey Store", "Marine Data", 3),
]
# Project names. People use these in Slack and in meetings; the service page gives them
# once, so finding what's been said about a service can take a second search.
CODENAMES = [
    "Kestrel", "Mitre", "Neap", "Fulmar", "Skerry", "Gannet", "Spindrift", "Thole",
    "Cormorant", "Bollard", "Sextant", "Petrel", "Shearwater", "Dunlin", "Curlew",
    "Merganser", "Eider", "Whimbrel", "Sanderling", "Turnstone", "Plover", "Avocet",
    "Razorbill", "Guillemot",
]  # fmt: skip
SLO = {1: 99.95, 2: 99.9, 3: 99.5}
IMPACT = {
    "Vessel Traffic": "vessel movements delayed",
    "Pilotage": "pilot jobs rebooked",
    "Harbour Dues": "invoices held",
    "Port Security": "gate passes refused",
    "Lock Operations": "lock transits delayed",
    "Marine Data": "readings lost",
    "Customer Contact": "customer contacts failed",
    "Infrastructure": "messages dropped",
}
# (key, what went wrong, how people describe it, two follow-up actions)
CAUSES = [
    ("cert", "the TLS certificate on {part} expired overnight and nothing alerted on it",
     "an expired certificate",
     ["Automate certificate renewal for {part}", "Alert 21 days before any certificate expires"]),
    ("pool", "a release raised the worker count on {part} without raising the database pool",
     "connection pool exhaustion",
     ["Derive the pool size from the worker count", "Alert when the pool is over 80% used"]),
    ("failover", "the standby database for {part} failed to promote during a planned failover",
     "a failed database failover",
     ["Rehearse failover every quarter", "Add a promotion check to the failover runbook"]),
    ("dns", "a DNS change left {part} resolving to a decommissioned load balancer",
     "a bad DNS change",
     ["Lower the TTL before planned DNS changes", "Add a synthetic check for {part}"]),
    ("flag", "a feature flag meant for staging was switched on in production for {part}",
     "a feature flag switched on in production",
     ["Separate flag environments", "Require a second reviewer for production flags"]),
    ("disk", "the log volume on {part} filled up and writes stopped",
     "a full disk",
     ["Rotate logs daily on {part}", "Alert at 80% disk use"]),
    ("lock", "a schema migration took a table lock on {part} during the morning peak",
     "a schema migration lock",
     ["Run migrations outside the morning peak", "Use online schema changes"]),
    ("queue", "replaying a backlog overwhelmed the queue consumers of {part}",
     "a queue backlog after a replay",
     ["Rate limit replays", "Add a dead letter queue for {part}"]),
    ("clock", "clock drift on {part} hosts made messages arrive out of order",
     "clock drift",
     ["Move {part} hosts to the managed time service", "Alert on clock skew over 200 ms"]),
    ("leak", "a memory leak in a new release of {part} restarted its pods repeatedly",
     "a memory leak",
     ["Add a soak test to the release pipeline", "Set memory alerts per pod"]),
]  # fmt: skip
PARTS = ["the API tier", "the ingest workers", "the primary database", "the message broker"]
VENDORS = [
    "Northlight Telemetry", "Saltmarsh Cloud", "Quayside Payments", "Fathom Mapping",
    "Keelhaul Security", "Brightwater Support", "Lodestar Radio", "Tidewell Sensors",
    "Anchorage Analytics", "Mariner Print", "Seawall Networks", "Capstan Consulting",
    "Halyard Hosting", "Bilge Pump Software", "Compass Rose Data", "Portside Identity",
    "Gantry Monitoring", "Windlass Mail", "Spinnaker Backup", "Rudder Payroll",
    "Beacon Point Legal", "Starboard Cleaning", "Hawser Logistics", "Trawler Translation",
]  # fmt: skip
TECH_VENDORS = [
    "Northlight Telemetry", "Saltmarsh Cloud", "Fathom Mapping", "Lodestar Radio",
    "Tidewell Sensors", "Anchorage Analytics", "Seawall Networks", "Halyard Hosting",
    "Gantry Monitoring",
]  # fmt: skip
POLICIES = {
    "Travel and subsistence policy": [
        (date(2024, 4, 1), {"London": 180, "Aberdeen": 120, "Rotterdam": 170}, 24),
        (date(2025, 4, 1), {"London": 205, "Aberdeen": 135, "Rotterdam": 185}, 28),
        (date(2026, 4, 1), {"London": 225, "Aberdeen": 150, "Rotterdam": 210}, 32),
    ],
}
STANDBY = [  # effective, weekly allowance, per call-out
    (date(2024, 4, 1), 120, 30),
    (date(2025, 7, 1), 135, 35),
    (date(2026, 4, 1), 165, 45),
]
RETENTION = [  # effective, AIS track days, CCTV days, call recording days
    (date(2024, 4, 1), 365, 31, 90),
    (date(2025, 10, 1), 730, 31, 180),
    (date(2026, 6, 1), 730, 45, 180),
    # Published, then withdrawn before it took effect: the highest version isn't the rule.
    (date(2026, 10, 1), 1095, 60, 365),
]
RETENTION_WITHDRAWN = (4, date(2026, 9, 21))  # which version, when
FREEZE = [  # effective, days before quarter end the change freeze starts, who can waive
    (date(2024, 4, 1), 5, "the Head of Port Systems"),
    (date(2025, 9, 1), 7, "the duty Service Manager"),
    (date(2026, 7, 1), 10, "the duty Service Manager and the Head of Port Systems together"),
]


@dataclass
class Person:
    name: str
    role: str
    base: str
    moves: list[tuple[date, str]]  # (from, team), oldest first
    joined: date = START
    left: date | None = None

    def team_on(self, d: date) -> str:
        team = self.moves[0][1]
        for when, name in self.moves:
            if when <= d:
                team = name
        return team

    @property
    def first(self) -> str:
        return self.name.split()[0]

    @property
    def initial(self) -> str:
        first, last = self.name.split()
        return f"{first[0]}. {last}"


@dataclass
class Service:
    name: str
    tier: int
    owners: list[tuple[date, str]]  # (from, team), oldest first
    rto_hours: list[tuple[date, int]]  # (from, value), oldest first
    rpo_minutes: int | None
    database: str
    region: str
    vendor: str
    pool_size: int
    port: int
    page_reviewed: date  # the service page says what was true then
    codename: str = ""
    depends_on: list[str] = field(default_factory=list)
    # A handover that was announced and then called off: (announced, team, called off).
    reversal: tuple[date, str, date] | None = None

    def owner_on(self, d: date) -> str:
        team = self.owners[0][1]
        for when, name in self.owners:
            if when <= d:
                team = name
        return team

    def rto_on(self, d: date) -> int:
        value = self.rto_hours[0][1]
        for when, hours in self.rto_hours:
            if when <= d:
                value = hours
        return value

    @property
    def slug(self) -> str:
        return slug(self.name)


@dataclass
class Action:
    text: str
    owner: str
    due: date
    closed: date | None = None
    reassigned: tuple[date, str] | None = None  # (when, to whom)

    def owner_on(self, d: date) -> str:
        return self.reassigned[1] if self.reassigned and self.reassigned[0] <= d else self.owner

    def open_on(self, d: date) -> bool:
        return self.closed is None or self.closed > d


@dataclass
class Incident:
    id: str
    day: date
    start: str
    end: str
    minutes: int
    severity: int
    service: str
    part: str
    cause: str
    cause_text: str
    cause_short: str
    lead: str
    comms: str
    impact: int
    impact_label: str
    actions: list[Action]
    corrected_impact: int | None = None  # a later correction in Slack
    corrected_on: date | None = None

    @property
    def true_impact(self) -> int:
        return self.corrected_impact if self.corrected_impact is not None else self.impact

    @property
    def has_postmortem(self) -> bool:
        return self.severity <= 2

    @property
    def style(self) -> str:
        """House style of the postmortem: prose, then labelled lines, then a table."""
        return (
            "prose" if self.day < date(2025, 10, 1)
            else "lines" if self.day < date(2026, 4, 1)
            else "table"
        )  # fmt: skip


@dataclass
class Vendor:
    name: str
    provides: str
    owner: str  # team
    risk: str
    renewal: date  # what the vendor page says
    notice_days: int
    annual_value: int  # restricted
    variation: tuple[date, date, int | None] | None = None  # signed, new renewal, new notice

    relative: bool = False  # the variation says "by 12 months" and gives no date

    @property
    def true_renewal(self) -> date:
        return self.variation[1] if self.variation else self.renewal

    @property
    def true_notice(self) -> int:
        return (self.variation[2] or self.notice_days) if self.variation else self.notice_days


@dataclass
class Decision:
    id: str
    title: str
    decided: date
    decider: str
    choice: str
    reason: str
    superseded_by: str | None = None


@dataclass
class Doc:
    collection: str
    filename: str
    title: str
    text: str


@dataclass
class World:
    people: dict[str, Person]
    leads: dict[str, list[tuple[date, str]]]  # team -> (from, person)
    head: list[tuple[date, str]]
    services: dict[str, Service]
    incidents: list[Incident]
    rota: dict[date, tuple[str, str]]  # Monday -> (primary, secondary), as planned
    swaps: list[tuple[date, str, str, date, str]]  # week, slot, new person, announced, old
    availability: dict[tuple[date, str], float]  # as first reported
    corrections: list[tuple[date, str, float, date]]  # month, service, true value, posted
    vendors: dict[str, Vendor]
    decisions: dict[str, Decision]
    budgets: list[tuple[date, int, int]]  # forecast date, capital £k, revenue £k
    public_budget: tuple[date, str]
    pay_bands: dict[str, tuple[int, int]]
    pen_tests: list[dict]
    go_lives: dict[str, tuple[date, date]]  # service -> (public date, restricted newer date)
    reorg: dict
    rng: random.Random = field(default_factory=lambda: random.Random(SEED))
    posted: dict = field(default_factory=dict)  # what Slack said, for the gold's evidence

    def lead_on(self, team: str, d: date) -> str:
        who = self.leads[team][0][1]
        for when, name in self.leads[team]:
            if when <= d:
                who = name
        return who

    def head_on(self, d: date) -> str:
        who = self.head[0][1]
        for when, name in self.head:
            if when <= d:
                who = name
        return who

    def on_call(self, d: date, slot: str = "primary") -> str:
        """Who actually held the slot in the week of d, after swaps."""
        week = monday_of(d)
        i = 0 if slot == "primary" else 1
        who = self.rota[week][i]
        for wk, s, person, _, _ in self.swaps:
            if wk == week and s == slot:
                who = person
        return who

    def true_availability(self, month: date, service: str) -> float:
        for m, s, value, _ in self.corrections:
            if m == month and s == service:
                return value
        return self.availability[(month, service)]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ── building the truth ───────────────────────────────────────────────────────
def build(seed: int = SEED) -> World:
    rng = random.Random(seed)
    names = list(PEOPLE)
    rng.shuffle(names)

    people: dict[str, Person] = {}
    leads: dict[str, list[tuple[date, str]]] = {}
    # Five people per team, the first a lead.
    for i, name in enumerate(names[: len(TEAMS) * 5]):
        team = TEAMS[i % len(TEAMS)]
        role = "Team Lead" if i < len(TEAMS) else rng.choice(ROLES)
        people[name] = Person(name, role, rng.choice(BASES), [(START, team)])
        if i < len(TEAMS):
            leads[team] = [(START, name)]
    head_name, deputy = names[len(TEAMS) * 5], names[len(TEAMS) * 5 + 1]
    people[head_name] = Person(
        head_name, "Head of Port Systems", "Pier House", [(START, "Leadership")]
    )
    people[deputy] = Person(deputy, "Service Manager", "Quay Offices", [(START, "Leadership")])

    # Moves between teams, and changes of lead.
    movers = rng.sample([p for p in people.values() if p.role in ROLES], 14)
    for p in movers:
        when = _first_of_month(rng, date(2025, 3, 1), date(2026, 9, 1))
        p.moves.append((when, rng.choice([t for t in TEAMS if t != p.moves[0][1]])))
    for team in rng.sample(TEAMS, 5):
        when = _first_of_month(rng, date(2025, 5, 1), date(2026, 8, 1))
        candidates = [
            p
            for p in people.values()
            if p.role in ROLES and p.team_on(when) == team and p.team_on(END) == team
        ]
        new_lead = rng.choice(candidates)
        leads[team].append((when, new_lead.name))
        old = people[leads[team][0][1]]
        old.role = "Principal Engineer"
        new_lead.role = "Team Lead"
    head = [(START, head_name), (date(2026, 2, 2), deputy)]

    services: dict[str, Service] = {}
    codenames = list(CODENAMES)
    rng.shuffle(codenames)
    for (name, team, tier), codename in zip(SERVICES, codenames, strict=True):
        owners = [(START, team)]
        rto0 = {1: 2, 2: 8, 3: 24}[tier]
        rto = [(START, rto0)]
        reviewed = _first_of_month(rng, date(2025, 1, 1), date(2025, 12, 1))
        services[name] = Service(
            name=name,
            tier=tier,
            owners=owners,
            rto_hours=rto,
            rpo_minutes=rng.choice([5, 15, 30, 60]) if rng.random() < 0.75 else None,
            database=rng.choice(["Postgres 15 on RDS", "Postgres 16 on RDS", "Aurora MySQL 3"]),
            region=rng.choice(["eu-west-2", "eu-west-1"]),
            vendor=rng.choice(TECH_VENDORS),
            pool_size=rng.choice([20, 30, 40, 60, 80, 120]),
            port=rng.randrange(8100, 8990, 10),
            page_reviewed=reviewed,
            codename=codename,
        )
    names_only = list(services)
    for s in services.values():
        s.depends_on = rng.sample([n for n in names_only if n != s.name], 2)
    shuffled = rng.sample(list(services.values()), 13)
    for s in shuffled[:10]:  # handovers
        when = _first_of_month(rng, date(2025, 4, 1), date(2026, 2, 1))
        s.owners.append((when, rng.choice([t for t in TEAMS if t != s.owners[0][1]])))
    for s in shuffled[:3]:  # and a second one
        when = _first_of_month(rng, s.owners[-1][0] + timedelta(days=120), date(2026, 8, 1))
        s.owners.append((when, rng.choice([t for t in TEAMS if t not in dict(s.owners).values()])))
    for s in shuffled[10:]:  # announced, then called off
        planned = _first_of_month(rng, date(2025, 6, 1), date(2026, 8, 1))
        announced = planned - timedelta(days=rng.randint(4, 12))
        team = rng.choice([t for t in TEAMS if t != s.owners[0][1]])
        s.reversal = (announced, team, announced + timedelta(days=rng.randint(9, 25)))
    for s in rng.sample(list(services.values()), 9):  # recovery targets tightened or relaxed
        when = _first_of_month(rng, date(2025, 6, 1), date(2026, 9, 1))
        s.rto_hours.append((when, max(1, s.rto_hours[0][1] + rng.choice([-1, 2, 4]))))

    rota_pool = [p.name for p in people.values() if p.role in ROLES + ["Principal Engineer"]]
    rota: dict[date, tuple[str, str]] = {}
    week = START
    while week <= END:
        primary, secondary = rng.sample(rota_pool, 2)
        rota[week] = (primary, secondary)
        week += timedelta(days=7)
    swaps = []
    for week in rng.sample(sorted(rota), 30):
        slot = rng.choice(["primary", "secondary"])
        old = rota[week][0 if slot == "primary" else 1]
        new = rng.choice([n for n in rota_pool if n not in rota[week]])
        swaps.append((week, slot, new, week - timedelta(days=rng.randint(2, 6)), old))
    swaps.sort()

    incidents = _incidents(rng, people, services, leads)
    availability, corrections = _availability(rng, services, incidents)
    vendors = _vendors(rng)
    decisions = _decisions(rng, people)

    budgets = []
    capital, revenue = 4180, 9320
    for d in (date(2025, 4, 7), date(2025, 7, 7), date(2025, 10, 6), date(2026, 1, 12),
              date(2026, 4, 6), date(2026, 7, 6), date(2026, 10, 5)):  # fmt: skip
        capital += rng.randrange(-190, 240, 10)
        revenue += rng.randrange(-260, 310, 10)
        budgets.append((d, capital, revenue))
    public = (date(2026, 4, 29), f"about £{round(budgets[4][1] / 1000, 1)}m")
    pay_bands = {
        "Developer": (41500, 52500),
        "Senior Developer": (52500, 68000),
        "Site Reliability Engineer": (55000, 71500),
        "Team Lead": (64000, 79000),
        "Principal Engineer": (72000, 88500),
    }
    pen_tests = [
        {"service": s, "tester": "Keelhaul Security", "date": d, "critical": c, "high": h,
         "finding": f, "public": p}
        for s, d, c, h, f, p in [
            ("Gate Access Control", date(2026, 3, 16), 1, 3,
             "badge replay against the turnstile controllers", "no critical findings remain open"),
            ("Port Pass", date(2026, 3, 23), 0, 2,
             "weak session expiry on the visitor kiosk", "testing completed in March"),
            ("Dues Invoicing", date(2026, 5, 11), 2, 1,
             "invoice export readable without authentication", "testing completed in May"),
            ("AIS Gateway", date(2026, 6, 8), 1, 4,
             "unsigned position messages accepted from the test feed", "testing completed in June"),
        ]
    ]  # fmt: skip
    go_lives = {
        "Lock Booking Portal": (date(2026, 10, 12), date(2026, 11, 9)),
        "Pilot Transfer Tracker": (date(2026, 9, 21), date(2026, 10, 20)),
        "Tide Alerts": (date(2026, 11, 2), date(2026, 11, 30)),
    }
    reorg = {
        "merged": "Pilotage",
        "into": "Vessel Traffic",
        "effective": date(2027, 1, 4),
        "minuted": date(2026, 9, 24),
    }
    return World(
        people, leads, head, services, incidents, rota, swaps, availability, corrections,
        vendors, decisions, budgets, public, pay_bands, pen_tests, go_lives, reorg, rng,
    )  # fmt: skip


def _first_of_month(rng: random.Random, lo: date, hi: date) -> date:
    return rng.choice(months(lo, hi))


def _incidents(rng, people, services, leads) -> list[Incident]:
    days = sorted(START + timedelta(days=rng.randrange((END - START).days)) for _ in range(84))
    out: list[Incident] = []
    count: dict[int, int] = {}
    for day in days:
        if day.weekday() >= 5:
            day -= timedelta(days=2)
        service = rng.choice(list(services.values()))
        owner = service.owner_on(day)
        severity = rng.choices([1, 2, 3], weights=[2, 4, 5])[0]
        key, text, short, follow = rng.choice(CAUSES)
        part = rng.choice(PARTS)
        count[day.year] = count.get(day.year, 0) + 1
        hour, minute = rng.randint(10, 21), rng.randrange(0, 60, 1)
        minutes = rng.randint(14, 230)
        end_at = hour * 60 + minute + minutes
        team_people = [p.name for p in people.values() if p.team_on(day) == owner]
        lead = rng.choice(team_people or list(people))
        comms = rng.choice([n for n in people if n != lead])
        actions = []
        for line in follow:
            owner_name = rng.choice(team_people or list(people))
            due = day + timedelta(days=rng.randint(21, 70))
            action = Action(line.format(part=part), owner_name, due)
            roll = rng.random()
            if roll < 0.55:
                action.closed = min(due + timedelta(days=rng.randint(-14, 20)), END)
                action.closed = max(action.closed, day + timedelta(days=7))
            elif roll < 0.75:
                moved = day + timedelta(days=rng.randint(10, 40))
                if moved < END:
                    action.reassigned = (moved, rng.choice([n for n in people if n != owner_name]))
            actions.append(action)
        impact = rng.randint(18, 940)
        out.append(
            Incident(
                id=f"PS-{day.year}-{count[day.year]:03d}",
                day=day,
                start=f"{hour:02d}:{minute:02d}",
                end=f"{end_at // 60 % 24:02d}:{end_at % 60:02d}",
                minutes=minutes,
                severity=severity,
                service=service.name,
                part=part,
                cause=key,
                cause_text=text.format(part=part),
                cause_short=short,
                lead=lead,
                comms=comms,
                impact=impact,
                impact_label=IMPACT[service.owners[0][1]],
                actions=actions,
            )
        )
    for inc in rng.sample([i for i in out if i.has_postmortem], 7):
        inc.corrected_impact = inc.impact + rng.choice([-1, 1]) * rng.randint(
            9, max(10, inc.impact // 3)
        )
        inc.corrected_impact = max(3, inc.corrected_impact)
        inc.corrected_on = min(inc.day + timedelta(days=rng.randint(8, 30)), END)
    return out


def _availability(rng, services, incidents):
    table: dict[tuple[date, str], float] = {}
    for month in months():
        for s in services.values():
            slo = SLO[s.tier]
            down = sum(
                i.minutes for i in incidents
                if i.service == s.name and (i.day.year, i.day.month) == (month.year, month.month)
                and i.severity <= 2
            )  # fmt: skip
            noise = rng.uniform(0, (100 - slo) * 0.6)
            value = 100 - noise - down / (30 * 24 * 60) * 100
            table[(month, s.name)] = round(value, 2)
    corrections = []
    keys = sorted(table, key=lambda k: (k[0], k[1]))
    for month, name in rng.sample(keys, 8):
        slo = SLO[services[name].tier]
        reported = table[(month, name)]
        # A correction that crosses the target, so it changes the answer to "did it miss?"
        value = round(slo - 0.07, 2) if reported >= slo else round(slo + 0.02, 2)
        corrections.append((month, name, value, month + timedelta(days=rng.randint(38, 55))))
    return table, sorted(corrections)


def _vendors(rng) -> dict[str, Vendor]:
    provides = [
        "AIS receiver maintenance",
        "cloud hosting",
        "card payments",
        "chart data",
        "penetration testing",
        "out-of-hours service desk",
        "VHF radio links",
        "tide gauge sensors",
        "usage analytics",
        "printed notices",
        "harbour wi-fi",
        "technical assurance",
        "backup hosting",
        "billing software",
        "bathymetry data",
        "identity verification",
        "uptime monitoring",
        "transactional email",
        "offsite backup",
        "payroll",
        "legal advice",
        "office cleaning",
        "courier delivery",
        "translation",
    ]
    out: dict[str, Vendor] = {}
    for name, what in zip(VENDORS, provides, strict=True):
        renewal = date(rng.choice([2026, 2027]), rng.randint(1, 12), rng.choice([1, 15, 28]))
        if renewal < date(2026, 11, 1):
            renewal = date(2027, renewal.month, renewal.day)
        out[name] = Vendor(
            name, what, rng.choice(TEAMS), rng.choice(["Low", "Medium", "High"]), renewal,
            rng.choice([30, 60, 90, 120]), rng.randrange(8000, 240000, 500),
        )  # fmt: skip
    for v in rng.sample(list(out.values()), 9):
        signed = _first_of_month(rng, date(2026, 2, 1), date(2026, 9, 1)) + timedelta(days=9)
        new_renewal = date(v.renewal.year + 1, v.renewal.month, v.renewal.day)
        v.variation = (signed, new_renewal, rng.choice([None, 45, 180]))
        v.relative = rng.random() < 0.5
    return out


TOPICS = [
    ("message broker for AIS events", ["RabbitMQ", "Amazon MQ", "Kafka on MSK"]),
    ("telemetry time-series store", ["InfluxDB", "Timescale", "Amazon Timestream"]),
    ("secrets management", ["Vault", "AWS Secrets Manager"]),
    ("container orchestration", ["ECS on Fargate", "EKS"]),
    ("single sign-on provider", ["Keycloak", "Entra ID"]),
    ("feature flag service", ["Unleash", "LaunchDarkly", "Flipt"]),
    ("log aggregation", ["OpenSearch", "Grafana Loki"]),
    ("API gateway", ["Kong", "Amazon API Gateway"]),
    ("infrastructure as code tool", ["Terraform", "OpenTofu"]),
    ("object storage for survey files", ["S3 Standard", "S3 Intelligent-Tiering"]),
]
WHY = [
    "the operating cost came in lower",
    "the team already had the skills",
    "it removed a server we had to patch",
    "it met the 2026 audit recommendation",
    "the licence terms changed",
]


def _decisions(rng, people) -> dict[str, Decision]:
    out: dict[str, Decision] = {}
    number = 10
    deciders = [p.name for p in people.values() if p.role == "Team Lead"]
    for topic, choices in TOPICS:
        day = _first_of_month(rng, date(2025, 1, 1), date(2025, 6, 1)) + timedelta(
            days=rng.randint(3, 24)
        )
        previous = None
        for choice in choices:
            number += rng.randint(1, 3)
            d = Decision(
                f"TDR-{number:03d}", f"Use {choice} for the {topic}", day, rng.choice(deciders),
                choice, rng.choice(WHY),
            )  # fmt: skip
            out[d.id] = d
            if previous:
                previous.superseded_by = d.id
            previous = d
            day = min(day + timedelta(days=rng.randint(120, 260)), END)
    return out
