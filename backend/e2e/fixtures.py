"""Realistic source documents for the end-to-end scenario (e2e/README.md): Northwind
Robotics (a warehouse robot maker) and its partner Helix Labs. Formats: md, docx, pdf,
xlsx, csv, html, txt, pptx, eml, png (a scanned page). Needs LibreOffice (`soffice`) for
the PDF.

usage: python e2e/fixtures.py OUT_DIR
"""

import csv
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation

OUT = Path(sys.argv[1])
NW = OUT / "northwind"
HX = OUT / "helix"
for d in (
    NW / "handbook",
    NW / "engineering",
    NW / "vendors",
    NW / "leadership",
    NW / "later",
    HX,
    OUT / "attach",
):
    d.mkdir(parents=True, exist_ok=True)


def _font(size: int):
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def docx(path: Path, title: str, blocks: list) -> None:
    doc = Document()
    doc.add_heading(title, 0)
    for b in blocks:
        if isinstance(b, tuple) and b[0] == "h":
            doc.add_heading(b[1], level=1)
        elif isinstance(b, tuple) and b[0] == "table":
            rows = b[1]
            t = doc.add_table(rows=len(rows), cols=len(rows[0]))
            t.style = "Table Grid"
            for i, row in enumerate(rows):
                for j, cell in enumerate(row):
                    t.cell(i, j).text = cell
        elif isinstance(b, tuple) and b[0] == "bullets":
            for item in b[1]:
                doc.add_paragraph(item, style="List Bullet")
        else:
            doc.add_paragraph(b)
    doc.save(path)


# ───────────────────────── Northwind: Company Handbook (org-wide) ─────────────────────────

(NW / "handbook" / "employee-handbook.md").write_text("""# Northwind Robotics Employee Handbook

Last updated: January 2025. Owner: People team (Head of People).

## Time off

**Paid time off.** Full-time employees receive 25 days of paid time off (PTO) per calendar
year, accrued monthly. Up to 5 unused days carry over into the next year and must be used
by March 31; anything above 5 days is forfeited on January 1.

**Sick leave.** Sick leave is not capped. Tell your manager the same day. Absences longer
than 3 consecutive working days need a doctor's note sent to the People team.

**Company holidays.** We observe 11 US holidays plus a company-wide shutdown between
December 24 and January 1.

## Parental leave (United States)

Employees based in the United States who have completed 90 days of employment receive
16 weeks of fully paid leave as a primary caregiver, or 8 weeks of fully paid leave as a
secondary caregiver, to be taken within 12 months of the birth or adoption. Leave for
employees outside the United States follows local law and the country addendum for that
office, where one exists.

## Remote work

Employees may work remotely up to 3 days a week with their manager's agreement. Fully
remote roles are marked as such in the offer letter. Remote employees receive a one-time
home-office stipend of $600 and $50 per month toward internet costs.

## Equipment

Laptops are refreshed every 3 years. A lost or stolen laptop must be reported to IT
Security within 24 hours, in the #it-help Slack channel and by email to
security@northwind.test. Do not try to wipe the device yourself.

## Conduct and reporting concerns

Report harassment or discrimination to your manager, to the Head of People, or
anonymously through the EthicsPoint hotline. Retaliation is prohibited.
""")

docx(
    NW / "handbook" / "travel-and-expense-policy.docx",
    "Travel, Expense and Procurement Policy",
    [
        "Version 4.1, effective 1 March 2026. Policy owner: Head of Finance.",
        ("h", "1. Travel"),
        "Book travel through Navan. Economy class is the default. Premium economy is allowed for "
        "flights longer than 6 hours. Business class requires written approval from a VP before booking.",
        "Hotels: up to $250 per night in the United States, and up to $300 per night in New York, "
        "San Francisco and London.",
        "Meals: a per diem of $75 per day for domestic travel and $110 per day for international travel. "
        "Alcohol is not reimbursed except at approved client dinners.",
        ("h", "2. Expenses"),
        "Submit expenses in Expensify with itemized receipts within 30 days of the purchase. Expenses "
        "submitted more than 60 days after the purchase are not reimbursed.",
        ("h", "3. Approval matrix"),
        "Every expense, purchase order and sponsorship needs approval before money is committed, "
        "according to its total value:",
        (
            "table",
            [
                ["Total value", "Approver"],
                ["Up to $1,000", "Direct manager"],
                ["$1,000 to $5,000", "Department head"],
                ["$5,000 to $25,000", "Head of Finance"],
                ["Above $25,000", "Chief Financial Officer (CFO)"],
            ],
        ),
        "Conference and event sponsorships count as vendor spend and follow the same matrix.",
        ("h", "4. Procurement of new vendors"),
        (
            "bullets",
            [
                "Any new vendor contract above $10,000 per year that will process or store customer data "
                "requires a security review by the Security Lead before signature.",
                "Vendors that process customer data must provide a current SOC 2 Type II report. A SOC 2 "
                "Type I report is not sufficient.",
                "The notice period for non-renewal or termination must be 90 days or less.",
                "Automatic renewal clauses require sign-off from Finance (the Head of Finance or the CFO).",
                "Contracts are signed by the CFO; nobody else may sign vendor contracts.",
            ],
        ),
    ],
)

with open(NW / "handbook" / "team-directory.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Name", "Title", "Team", "Email", "Location", "Manager"])
    for row in [
        (
            "Priya Raman",
            "Chief Executive Officer",
            "Executive",
            "priya.raman@northwind.test",
            "San Francisco",
            "",
        ),
        (
            "Daniel Reyes",
            "Chief Financial Officer",
            "Finance",
            "daniel.reyes@northwind.test",
            "San Francisco",
            "Priya Raman",
        ),
        (
            "Amara Okafor",
            "Head of Finance",
            "Finance",
            "amara.okafor@northwind.test",
            "Austin",
            "Daniel Reyes",
        ),
        (
            "Jonah Whitfield",
            "Senior Accountant",
            "Finance",
            "jonah.whitfield@northwind.test",
            "Austin",
            "Amara Okafor",
        ),
        (
            "Marcus Bell",
            "Head of Operations",
            "Operations",
            "marcus.bell@northwind.test",
            "Austin",
            "Priya Raman",
        ),
        (
            "Grace Mensah",
            "Procurement Manager",
            "Operations",
            "grace.mensah@northwind.test",
            "Austin",
            "Marcus Bell",
        ),
        (
            "Sofia Lindqvist",
            "VP Engineering",
            "Engineering",
            "sofia.lindqvist@northwind.test",
            "San Francisco",
            "Priya Raman",
        ),
        (
            "Kenji Watanabe",
            "Head of Platform",
            "Platform",
            "kenji.watanabe@northwind.test",
            "Seattle",
            "Sofia Lindqvist",
        ),
        (
            "Ines Carvalho",
            "Security Lead",
            "Security",
            "ines.carvalho@northwind.test",
            "Lisbon",
            "Sofia Lindqvist",
        ),
        (
            "Rachel Moore",
            "Head of People",
            "People",
            "rachel.moore@northwind.test",
            "Austin",
            "Priya Raman",
        ),
        (
            "Lena Fischer",
            "Senior Software Engineer",
            "Platform",
            "lena.fischer@northwind.test",
            "Berlin",
            "Kenji Watanabe",
        ),
        (
            "Tom Alvarez",
            "Contract Technical Writer",
            "Engineering",
            "tom.alvarez@northwind.test",
            "Remote",
            "Sofia Lindqvist",
        ),
        (
            "Hiro Tanaka",
            "Staff Engineer",
            "Fleet Control",
            "hiro.tanaka@northwind.test",
            "Seattle",
            "Sofia Lindqvist",
        ),
        (
            "Aisha Rahman",
            "Engineering Manager, Fleet Control",
            "Fleet Control",
            "aisha.rahman@northwind.test",
            "San Francisco",
            "Sofia Lindqvist",
        ),
        (
            "Pedro Alves",
            "Site Reliability Engineer",
            "Platform",
            "pedro.alves@northwind.test",
            "Lisbon",
            "Kenji Watanabe",
        ),
        (
            "Chloe Dubois",
            "Firmware Engineer",
            "Robotics",
            "chloe.dubois@northwind.test",
            "Berlin",
            "Martin Kowalski",
        ),
        (
            "Martin Kowalski",
            "Head of Robotics",
            "Robotics",
            "martin.kowalski@northwind.test",
            "Berlin",
            "Sofia Lindqvist",
        ),
        (
            "Nadia Petrov",
            "Customer Success Lead",
            "Customer Success",
            "nadia.petrov@northwind.test",
            "Austin",
            "Marcus Bell",
        ),
        (
            "Owen Clarke",
            "Support Engineer",
            "Customer Success",
            "owen.clarke@northwind.test",
            "Austin",
            "Nadia Petrov",
        ),
        (
            "Yara Haddad",
            "Data Engineer",
            "Data",
            "yara.haddad@northwind.test",
            "Seattle",
            "Kenji Watanabe",
        ),
        ("Ben Ito", "Recruiter", "People", "ben.ito@northwind.test", "Remote", "Rachel Moore"),
        (
            "Laura Chen",
            "General Counsel",
            "Legal",
            "laura.chen@northwind.test",
            "San Francisco",
            "Priya Raman",
        ),
    ]:
        w.writerow(row)

(NW / "handbook" / "benefits-overview.html").write_text("""<!doctype html>
<html><head><title>Benefits overview 2026</title></head><body>
<h1>Northwind Robotics benefits, 2026</h1>
<h2>Health</h2>
<p>Medical, dental and vision through Aetna Choice POS II. Northwind pays 90% of the premium
for employees and 75% for dependents. Coverage starts on your first day.</p>
<h2>Retirement</h2>
<p>401(k) through Guideline with a 4% match, vesting immediately.</p>
<h2>Learning and wellbeing</h2>
<ul>
<li>Learning budget: $1,500 per year for courses, books and conferences.</li>
<li>Gym or fitness reimbursement: $40 per month.</li>
<li>Mental health: 12 free therapy sessions per year through Spring Health.</li>
</ul>
</body></html>
""")

(
    NW / "handbook" / "onboarding-checklist.txt"
).write_text("""New hire onboarding checklist (all teams)

Day 1
- Collect your laptop from IT and turn on FileVault before signing in to anything else.
- Enroll a hardware security key for Okta (two are mailed to remote hires).
- Join #general, #it-help and your team channel in Slack.

Week 1
- Complete security awareness training in Vanta (due within 7 days).
- Read the Employee Handbook and the Information Security Policy.
- Set up Expensify and add your corporate card.

Month 1
- 30-day check-in with your manager.
- Engineers: shadow one on-call shift before joining the rotation.
""")

docx(
    NW / "handbook" / "information-security-policy.docx",
    "Information Security Policy",
    [
        "Version 3.2, effective 1 June 2026. Supersedes all earlier security guidance, including "
        "the equipment section of the Employee Handbook. Policy owner: Security Lead.",
        ("h", "1. Data classification"),
        "Data is classified as Public, Internal, Confidential or Restricted. Customer telemetry, maps "
        "of customer sites and customer contact data are Restricted.",
        ("h", "2. Endpoints"),
        "Full-disk encryption (FileVault or BitLocker) is mandatory on every laptop. Exceptions must "
        "be approved in writing by the Security Lead and expire after 30 days.",
        "Lost or stolen devices must be reported immediately, and in any case no later than 4 hours "
        "after you notice, by email to security@northwind.test and in #it-help. IT will lock and wipe "
        "the device remotely.",
        ("h", "3. Accounts and access"),
        "Multi-factor authentication is required on every company account. Hardware security keys "
        "are required for production access. Production access is reviewed every quarter by the "
        "Head of Platform.",
        ("h", "4. Incident severity"),
        (
            "table",
            [
                ["Severity", "Definition", "Response"],
                [
                    "SEV1",
                    "Robots stopped or customer data exposed",
                    "Page the incident commander immediately",
                ],
                [
                    "SEV2",
                    "Major feature degraded for several customers",
                    "Respond within 30 minutes",
                ],
                [
                    "SEV3",
                    "Minor degradation or a single customer affected",
                    "Respond within 4 hours",
                ],
                ["SEV4", "No customer impact", "Next business day"],
            ],
        ),
    ],
)

# ───────────────────────── Northwind: Engineering (org-wide) ─────────────────────────

POSTMORTEMS = [
    (
        "2026-01-14",
        "fleet-control-api-latency",
        "Fleet-control API latency",
        "SEV3",
        "47 minutes",
        "Hiro Tanaka",
        "Our Datadog monitor on p99 latency fired four minutes after the 14:02 deploy, and the on-call engineer was paged.",
        "The new release lowered the database connection pool size from 50 to 10, and the pool was exhausted under normal load.",
        "Dashboards loaded slowly for about 30 customers; no robots were affected.",
    ),
    (
        "2026-02-03",
        "telemetry-ingestion-lag",
        "Telemetry ingestion lag",
        "SEV2",
        "2 hours 10 minutes",
        "Pedro Alves",
        "Brightline Logistics' operations team opened a support ticket saying their fleet dashboard was 40 minutes stale. Our lag alert only fired 35 minutes after their ticket, because its threshold was set to 60 minutes.",
        "A configuration change lowered the Kafka consumer session timeout to 6 seconds, which put the consumer group into a rebalancing loop.",
        "Telemetry for 64 customers was delayed by up to 2 hours; no data was lost.",
    ),
    (
        "2026-02-27",
        "eu-ota-firmware-failure",
        "Firmware updates failing in the EU",
        "SEV2",
        "5 hours",
        "Chloe Dubois",
        "A firmware engineer noticed failed installs on the internal canary fleet in Berlin before the rollout reached customers.",
        "The code-signing certificate used for EU firmware builds had expired on February 26.",
        "No customer robots received the bad build; the EU rollout was delayed by a day.",
    ),
    (
        "2026-03-19",
        "sso-login-outage",
        "Single sign-on outage",
        "SEV1",
        "1 hour 25 minutes",
        "Ines Carvalho",
        "The PagerDuty synthetic login check failed at 09:12 and paged the security on-call.",
        "During a planned SAML certificate rotation, the new certificate was uploaded to Okta but the fleet-control service was still configured with the old one.",
        "No customer could sign in to the fleet console for 85 minutes. Robots kept running.",
    ),
    (
        "2026-04-08",
        "map-sync-corruption",
        "Map sync corruption",
        "SEV2",
        "6 hours",
        "Aisha Rahman",
        "A customer, Kestrel Foods, called our support line after robots kept routing into an aisle that had been closed for maintenance.",
        "A race condition in map tile cache invalidation let an old tile overwrite a newer one when two edits arrived within the same second.",
        "Three Kestrel Foods sites ran on outdated maps; two minor collisions with barriers, no injuries.",
    ),
    (
        "2026-04-30",
        "billing-export-duplicates",
        "Duplicate charges in billing export",
        "SEV2",
        "1 day",
        "Yara Haddad",
        "Our Finance team spotted duplicate lines while reconciling the April billing export during month-end close.",
        "The export job retried failed batches without an idempotency key, so 212 usage lines were written twice.",
        "Invoices for 9 customers were corrected before they were sent.",
    ),
    (
        "2026-05-21",
        "eu-dns-outage",
        "EU region DNS outage",
        "SEV2",
        "38 minutes",
        "Pedro Alves",
        "A Datadog synthetic check against eu-west-1 failed and paged the platform on-call within two minutes.",
        "A Terraform change removed the Route 53 record for the EU API while renaming a module.",
        "EU customers could not reach the fleet console or API for 38 minutes.",
    ),
    (
        "2026-06-11",
        "battery-telemetry-gaps",
        "Gaps in battery telemetry",
        "SEV3",
        "3 days",
        "Yara Haddad",
        "Northpoint Retail emailed their customer success manager asking why battery health charts had gaps; nothing in our monitoring had flagged it.",
        "A table TTL misconfiguration in ClickHouse filled the disk on one shard, which then silently dropped inserts.",
        "Battery health history for 11 customers has a three-day gap that could not be backfilled.",
    ),
    (
        "2026-07-02",
        "status-page-stale",
        "Status page not updating",
        "SEV4",
        "2 hours",
        "Owen Clarke",
        "A support engineer noticed during an unrelated incident that status page updates were not publishing.",
        "The status page API token had been revoked when its owner left the company.",
        "None beyond a delayed status update.",
    ),
    (
        "2026-07-25",
        "route-planner-errors",
        "Route planner error rate",
        "SEV3",
        "1 hour 5 minutes",
        "Hiro Tanaka",
        "The Sentry error-rate alert fired twenty minutes after release 4.18 reached all regions.",
        "A memory leak in the new path-smoothing code made route planner pods restart every few minutes.",
        "About 3% of route requests failed and were retried; robots paused briefly.",
    ),
    (
        "2026-08-14",
        "notification-email-delays",
        "Notification emails delayed",
        "SEV3",
        "9 hours",
        "Nadia Petrov",
        "Several customers reported through Zendesk that maintenance alert emails were arriving hours late; our dashboards showed nothing wrong.",
        "A marketing campaign used the same SendGrid sender pool as product notifications and exhausted its rate limit.",
        "Maintenance alerts for about 120 customers were delayed by up to 9 hours.",
    ),
    (
        "2026-09-03",
        "warehouse-fleet-stop",
        "Fleet stopped at a customer warehouse",
        "SEV1",
        "52 minutes",
        "Aisha Rahman",
        "Brightline Logistics' site manager in Reno phoned our support line when all 140 robots on the floor stopped at once.",
        "A feature flag meant for the internal test fleet was misconfigured and pushed the new obstacle model to every robot at the Reno site.",
        "One warehouse stopped picking for 52 minutes during peak shift.",
    ),
    (
        "2026-09-29",
        "ci-pipeline-outage",
        "CI pipeline outage",
        "SEV4",
        "4 hours",
        "Hiro Tanaka",
        "Engineers noticed in Slack that every build was failing after the runner image update.",
        "A GitHub Actions runner image update removed a system library our firmware build depends on.",
        "No customer impact; deploys were frozen for four hours.",
    ),
    (
        "2026-10-16",
        "rate-limiter-misfire",
        "API rate limiter misfire",
        "SEV2",
        "1 hour 40 minutes",
        "Pedro Alves",
        "A Datadog alert on 429 responses fired within five minutes of the change.",
        "A configuration change to the API gateway halved the per-customer request limit.",
        "Integrations for 22 customers were throttled; robots kept working.",
    ),
]
for date, slug, title, sev, duration, commander, detection, cause, impact in POSTMORTEMS:
    (NW / "engineering" / f"postmortem-{date}-{slug}.md").write_text(f"""# Postmortem: {title}

Date: {date}
Severity: {sev}
Duration: {duration}
Incident commander: {commander}

## Summary

{impact}

## How we found out

{detection}

## Root cause

{cause}

## Action items

- Add a regression test and a runbook entry for this failure (owner: {commander}).
- Review alert thresholds for the affected service within two weeks.
""")

(NW / "engineering" / "oncall-runbook.md").write_text("""# On-call runbook

## Rotation

The platform on-call rotation changes every Monday at 10:00 Pacific. Each week has a
primary and a secondary on-call engineer, scheduled in PagerDuty.

## Escalation

1. The primary on-call acknowledges a page within 5 minutes.
2. If the primary hasn't acknowledged within 10 minutes, PagerDuty pages the secondary.
3. For a SEV1, or if the secondary can't resolve it within 30 minutes, escalate to the
   Head of Platform, who acts as incident commander or assigns one.

## During a SEV1 or SEV2

- Open an incident channel named #inc-<date>-<short-name>.
- Post a status page update within 15 minutes and then every 30 minutes until resolved.
- Customer Success contacts affected customers directly for any SEV1.

## After the incident

A postmortem is due within 5 working days for every SEV1 and SEV2.
""")

(NW / "engineering" / "architecture-overview.md").write_text("""# Platform architecture overview

## Services

- **fleet-control**: the API robots and the fleet console talk to. Python, runs on EKS.
  Depends on PostgreSQL (Amazon RDS) for fleet state, Redis for robot sessions and Kafka
  for commands to robots.
- **telemetry pipeline**: robots publish telemetry to Kafka; consumers write it to
  ClickHouse, which backs every customer dashboard.
- **maps**: stores site maps in S3 and serves map tiles through a tile cache in front of it.
- **route planner**: computes paths for each robot; reads maps and fleet state.

## Regions

Production runs in AWS us-east-1 and eu-west-1. EU customers' data stays in eu-west-1.

## Observability

Metrics and synthetic checks in Datadog, errors in Sentry, paging through PagerDuty.
""")

# ───────────────────────── Northwind: Vendors & Contracts (restricted) ─────────────────────────

VENDORS = [
    (
        "AWS",
        "Cloud infrastructure",
        1240000,
        "2024-02-01",
        "2027-01-31",
        "Kenji Watanabe",
        "Yes",
        "2027-05-15",
        90,
        "No",
        "Active",
    ),
    (
        "Datadog",
        "Observability",
        186000,
        "2023-07-01",
        "2026-12-31",
        "Kenji Watanabe",
        "Yes",
        "2026-11-30",
        60,
        "Yes",
        "Active",
    ),
    (
        "PagerDuty",
        "Observability",
        42000,
        "2024-03-15",
        "2027-03-14",
        "Kenji Watanabe",
        "No",
        "2027-08-01",
        30,
        "Yes",
        "Active",
    ),
    (
        "Sentry",
        "Observability",
        28500,
        "2025-01-10",
        "2027-01-09",
        "Sofia Lindqvist",
        "Yes",
        "2027-02-28",
        30,
        "Yes",
        "Active",
    ),
    (
        "Snowflake",
        "Data platform",
        310000,
        "2024-09-01",
        "2027-08-31",
        "Kenji Watanabe",
        "Yes",
        "2027-01-20",
        90,
        "No",
        "Active",
    ),
    (
        "Zendesk",
        "Customer support",
        64000,
        "2025-01-15",
        "2027-01-15",
        "Marcus Bell",
        "Yes",
        "2026-12-10",
        45,
        "Yes",
        "Active",
    ),
    (
        "Gusto",
        "HR and payroll",
        38000,
        "2023-04-01",
        "2027-03-31",
        "Rachel Moore",
        "No",
        "2027-06-30",
        60,
        "Yes",
        "Active",
    ),
    (
        "Greenhouse",
        "HR and payroll",
        21000,
        "2024-06-01",
        "2027-05-31",
        "Rachel Moore",
        "No",
        "2027-04-15",
        30,
        "Yes",
        "Active",
    ),
    (
        "Expensify",
        "Finance",
        18400,
        "2024-01-01",
        "2026-12-31",
        "Amara Okafor",
        "No",
        "2027-09-30",
        30,
        "Yes",
        "Active",
    ),
    (
        "NetSuite",
        "Finance",
        96000,
        "2022-10-01",
        "2027-09-30",
        "Amara Okafor",
        "No",
        "2027-03-01",
        90,
        "No",
        "Active",
    ),
    (
        "Salesforce",
        "Sales",
        142000,
        "2023-11-01",
        "2027-10-31",
        "Marcus Bell",
        "Yes",
        "2027-07-31",
        30,
        "Yes",
        "Active",
    ),
    (
        "Okta",
        "Security",
        54000,
        "2024-05-01",
        "2027-04-30",
        "Ines Carvalho",
        "Yes",
        "2027-03-15",
        60,
        "Yes",
        "Active",
    ),
    (
        "CrowdStrike",
        "Security",
        88000,
        "2025-02-01",
        "2028-01-31",
        "Ines Carvalho",
        "No",
        "2027-10-31",
        90,
        "No",
        "Active",
    ),
    (
        "1Password",
        "Security",
        9600,
        "2024-08-01",
        "2027-07-31",
        "Ines Carvalho",
        "No",
        "2027-05-31",
        30,
        "Yes",
        "Active",
    ),
    (
        "GitHub",
        "Engineering tools",
        47000,
        "2023-03-01",
        "2027-02-28",
        "Sofia Lindqvist",
        "Yes",
        "2027-06-15",
        30,
        "Yes",
        "Active",
    ),
    (
        "Linear",
        "Engineering tools",
        12000,
        "2025-04-01",
        "2027-03-31",
        "Sofia Lindqvist",
        "No",
        "2027-01-31",
        30,
        "Yes",
        "Active",
    ),
    (
        "Figma",
        "Design",
        15500,
        "2024-10-01",
        "2026-09-30",
        "Sofia Lindqvist",
        "No",
        "2027-02-28",
        30,
        "Yes",
        "Terminated",
    ),
    (
        "Slack",
        "Collaboration",
        36000,
        "2023-06-01",
        "2027-05-31",
        "Marcus Bell",
        "Yes",
        "2027-04-30",
        30,
        "Yes",
        "Active",
    ),
    (
        "Google Workspace",
        "Collaboration",
        41000,
        "2022-01-01",
        "2027-12-31",
        "Marcus Bell",
        "Yes",
        "2027-09-15",
        30,
        "Yes",
        "Active",
    ),
    (
        "Twilio",
        "Communications API",
        73000,
        "2024-04-01",
        "2027-03-31",
        "Kenji Watanabe",
        "Yes",
        "2026-12-31",
        60,
        "Yes",
        "Active",
    ),
    (
        "Stripe",
        "Payments",
        52000,
        "2023-09-01",
        "2027-08-31",
        "Amara Okafor",
        "Yes",
        "2027-11-30",
        30,
        "Yes",
        "Active",
    ),
    (
        "DHL Supply Chain",
        "Logistics",
        410000,
        "2024-07-01",
        "2027-06-30",
        "Marcus Bell",
        "No",
        "N/A",
        120,
        "Yes",
        "Active",
    ),
    (
        "Flexport",
        "Logistics",
        265000,
        "2025-03-01",
        "2027-02-28",
        "Marcus Bell",
        "No",
        "2027-07-01",
        90,
        "No",
        "Active",
    ),
    (
        "Arrow Electronics",
        "Components",
        1850000,
        "2024-01-01",
        "2026-12-31",
        "Marcus Bell",
        "No",
        "N/A",
        180,
        "Yes",
        "Active",
    ),
    (
        "Mouser",
        "Components",
        320000,
        "2025-01-01",
        "2027-12-31",
        "Marcus Bell",
        "No",
        "N/A",
        60,
        "No",
        "Active",
    ),
    (
        "Cooley LLP",
        "Legal",
        120000,
        "2023-01-01",
        "2027-01-01",
        "Daniel Reyes",
        "No",
        "N/A",
        30,
        "No",
        "Active",
    ),
    (
        "Deloitte",
        "Audit",
        95000,
        "2025-05-01",
        "2027-04-30",
        "Daniel Reyes",
        "No",
        "N/A",
        60,
        "No",
        "Active",
    ),
    (
        "Vanta",
        "Security",
        24000,
        "2025-06-01",
        "2027-05-31",
        "Ines Carvalho",
        "Yes",
        "2027-02-15",
        30,
        "Yes",
        "Active",
    ),
    (
        "Segment",
        "Data platform",
        58000,
        "2024-02-01",
        "2026-08-31",
        "Kenji Watanabe",
        "Yes",
        "2026-10-31",
        30,
        "No",
        "Terminated",
    ),
    (
        "HubSpot",
        "Marketing",
        33000,
        "2024-11-01",
        "2027-10-31",
        "Marcus Bell",
        "Yes",
        "2027-01-05",
        30,
        "Yes",
        "Active",
    ),
]
wb = Workbook()
ws = wb.active
ws.title = "Vendors"
ws.append(
    [
        "Vendor",
        "Category",
        "Annual Value (USD)",
        "Contract Start",
        "Renewal Date",
        "Business Owner",
        "Customer Data Access",
        "SOC 2 Type II Expiry",
        "Notice Period (days)",
        "Auto-Renew",
        "Status",
    ]
)
for v in VENDORS:
    ws.append(list(v))
wb.save(NW / "vendors" / "vendor-register-2026.xlsx")

CONTRACTS = {
    "datadog": (
        "Datadog",
        """Contract summary: Datadog Master Subscription Agreement

Prepared by Grace Mensah (Procurement) for the 2026 renewal review.

Northwind signed Datadog's Master Subscription Agreement on 1 July 2023 for infrastructure
monitoring, APM and synthetic checks. The subscription term renews automatically for
successive twelve-month periods unless either party gives written notice of non-renewal at
least sixty (60) days before the end of the then-current term. Fees for a renewal term may
not increase by more than five percent (5%) over the fees for the preceding term.

There is no right to terminate for convenience; either party may terminate for an uncured
material breach on 30 days' notice. The agreement is governed by the laws of the State of
New York. Datadog processes customer telemetry metadata and holds a SOC 2 Type II report.""",
    ),
    "zendesk": (
        "Zendesk",
        """Contract summary: Zendesk Suite subscription

Prepared by Grace Mensah (Procurement).

Our Zendesk Suite Professional subscription (180 agent seats) started on 15 January 2025.
It renews automatically for one-year terms unless we give notice of non-renewal at least
45 days before the renewal date. On renewal, Zendesk may raise the subscription fees by no
more than seven percent (7%) over the prior year's fees; any larger increase requires an
amendment signed by both parties.

Northwind cannot terminate for convenience during a term. The agreement is governed by
the laws of the State of California. Zendesk stores customer contact details and support
conversations.""",
    ),
    "snowflake": (
        "Snowflake",
        """Contract summary: Snowflake capacity agreement

Prepared by Grace Mensah (Procurement).

Northwind purchased a three-year Snowflake capacity commitment starting 1 September 2024
(USD 310,000 per year, billed annually). The agreement does not renew automatically; a new
order form is required at the end of the term. Either party must give at least ninety (90)
days' written notice of termination at the end of the term (amended from thirty days by
Amendment No. 2). The agreement does not cap price increases for a future order form.

There is no termination for convenience. Governed by the laws of the State of Delaware.""",
    ),
    "arrow": (
        "Arrow Electronics",
        """Contract summary: Arrow Electronics component supply agreement

Prepared by Grace Mensah (Procurement).

Arrow supplies motor controllers, lidar modules and battery management boards under a
supply agreement effective 1 January 2024. The agreement renews automatically for one-year
terms unless either party gives notice at least one hundred eighty (180) days before the
renewal date. Prices are adjusted every quarter against Arrow's component market index, so
there is no fixed cap on price increases.

Northwind may terminate the agreement for convenience at any time on 180 days' written
notice, paying for components already ordered. Governed by the laws of the State of Texas.""",
    ),
    "okta": (
        "Okta",
        """Contract summary: Okta Workforce Identity

Prepared by Grace Mensah (Procurement).

Okta Workforce Identity (SSO, MFA and lifecycle management for 140 users) has been in place
since 1 May 2024. The subscription renews automatically for twelve months at a time unless
notice of non-renewal is given at least 60 days before the end of the term. Renewal pricing
may not increase by more than three percent (3%).

No termination for convenience. Governed by the laws of the State of California.""",
    ),
}
for slug, (_vendor, text) in CONTRACTS.items():
    if slug in ("datadog", "zendesk"):
        lines = text.split("\n\n")
        docx(NW / "vendors" / f"contract-summary-{slug}.docx", lines[0], lines[1:])
    else:
        (NW / "vendors" / f"contract-summary-{slug}.md").write_text("# " + text)

msg = EmailMessage()
msg["From"] = "Jordan Blake <jordan.blake@zendesk.example>"
msg["To"] = "Marcus Bell <marcus.bell@northwind.test>"
msg["Cc"] = "Grace Mensah <grace.mensah@northwind.test>"
msg["Subject"] = "Your Zendesk renewal on January 15, 2027"
msg["Date"] = "Tue, 20 Oct 2026 16:42:00 -0700"
msg.set_content("""Hi Marcus,

I hope you're well. I'm reaching out ahead of Northwind's Zendesk Suite renewal on
January 15, 2027.

As part of our 2027 pricing update, your renewal will include a 12% increase, taking the
annual subscription from $64,000 to $71,680 for the same 180 agent seats. The new pricing
also includes our AI agents add-on at no extra cost for the first year.

If you'd like to discuss a multi-year commitment to lock in pricing, I'm happy to set up
a call next week.

Best,
Jordan Blake
Senior Account Executive, Zendesk
""")
(NW / "vendors" / "zendesk-renewal-notice.eml").write_bytes(bytes(msg))

# A scanned signature page: OCR is the only way to read it.
img = Image.new("RGB", (2100, 2200), "white")
draw = ImageDraw.Draw(img)
font, big = _font(40), _font(46)
y = 160
draw.text((140, y), "AMENDMENT NO. 2 TO THE SNOWFLAKE CAPACITY AGREEMENT", font=big, fill="black")
y += 140
for line in [
    'This Amendment No. 2 is made between Snowflake Inc. ("Snowflake") and',
    'Northwind Robotics, Inc. ("Customer") and is effective September 1, 2026.',
    "",
    '1. Section 9.2 (Notice of Termination) is amended to replace "thirty (30)',
    '   days" with "ninety (90) days".',
    "",
    "2. Customer's capacity commitment for the remaining term is unchanged at",
    "   USD 310,000 per year.",
    "",
    "3. All other terms of the Agreement remain in full force and effect.",
    "",
    "",
    "Signed for Northwind Robotics, Inc.:      Signed for Snowflake Inc.:",
    "",
    "Daniel Reyes                              Jaya Patel",
    "Chief Financial Officer                   Regional Vice President",
    "Date: August 18, 2026                     Date: August 21, 2026",
]:
    draw.text((140, y), line, font=font, fill="black")
    y += 64
img = img.rotate(0.6, fillcolor="white")
img.save(NW / "vendors" / "snowflake-amendment-2-signed.png")

# ───────────────────────── Northwind: Leadership (restricted) ─────────────────────────

(
    NW / "leadership" / "compensation-bands-2026.md"
).write_text("""# Compensation bands 2026 (confidential)

Approved by the CEO and CFO on 12 February 2026. Base salary ranges in USD for US-based
roles; Berlin and Lisbon bands are set at 78% and 62% of these.

| Level | Title | Base salary range | Equity (4 years) |
|---|---|---|---|
| L3 | Software Engineer | $130,000 to $160,000 | 4,000 options |
| L4 | Senior Software Engineer | $160,000 to $195,000 | 8,000 options |
| L5 | Staff Engineer | $195,000 to $240,000 | 16,000 options |
| L6 | Principal Engineer | $240,000 to $290,000 | 30,000 options |

Offers above the top of a band need approval from the CFO.
""")

prs = Presentation()
for title, bullets in [
    (
        "Board update: Q3 2026",
        ["Northwind Robotics", "Prepared for the board meeting on 9 October 2026"],
    ),
    (
        "Financials",
        [
            "ARR $18.4M, up 41% year over year",
            "Gross margin 58%",
            "Runway 26 months at current burn",
        ],
    ),
    (
        "People",
        [
            "Headcount 124 (target 140 by end of 2026)",
            "Open a Berlin engineering office in Q2 2027 with 18 hires",
            "Germany employment policies to be drafted by the Head of People before the first hire",
        ],
    ),
    (
        "Risks",
        [
            "Arrow Electronics supply concentration: 61% of components",
            "Brightline Logistics is 22% of ARR",
        ],
    ),
]:
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    body = slide.placeholders[1].text_frame
    body.text = bullets[0]
    for b in bullets[1:]:
        body.add_paragraph().text = b
prs.save(NW / "leadership" / "board-update-q3-2026.pptx")

# Uploaded later, to fulfil a document request.
(NW / "later" / "germany-parental-leave-addendum.md").write_text("""# Country addendum: Germany

Applies to employees of Northwind Robotics GmbH (Berlin). Effective 1 November 2026.
Owner: Head of People.

## Parental leave

Employees in Germany are entitled to statutory parental leave (Elternzeit) of up to three
years per child, and to statutory parental allowance (Elterngeld). Northwind tops up
Elterngeld to 100% of the employee's net salary for the first 14 weeks of leave, for either
parent, after six months of employment.

## Maternity protection

Mutterschutz applies in full: six weeks before and eight weeks after birth, fully paid
through statutory maternity pay and Northwind's employer supplement.

## Vacation

Employees in Germany receive 28 days of paid vacation per year.
""")

# ───────────────────────── Helix Labs ─────────────────────────

(HX / "helix-security-handbook.md").write_text("""# Helix Labs Security Handbook

Version 2.0, effective 1 April 2026.

## Who this applies to

Every Helix employee and contractor, and partner personnel who handle Helix data or work
in Helix systems, including staff of partner companies on joint projects such as Project
Atlas.

## Lost or stolen devices

Report a lost or stolen device that has ever held Helix data to the Helix Security
Operations Center within one (1) hour, at soc@helixlabs.test or +1 415 555 0142, even if
the device belongs to your own employer.

## Model weights

Helix model weights are Restricted. They may only be stored in Helix-managed buckets and
must never be copied to robots, laptops or partner systems.
""")

docx(
    HX / "project-atlas-sow.docx",
    "Statement of Work: Project Atlas",
    [
        "Between Helix Labs, Inc. and Northwind Robotics, Inc. Effective 1 August 2026.",
        ("h", "Scope"),
        "Helix integrates its Vision-3 object detection model into Northwind's pick robots through "
        "a hosted inference API. Northwind provides robots, a pilot site and integration engineering.",
        ("h", "Milestones and payments"),
        (
            "table",
            [
                ["Milestone", "Date", "Payment from Northwind"],
                ["M1: pilot at Brightline Logistics, Reno", "15 December 2026", "$96,000"],
                ["M2: 3 customer sites live", "1 March 2027", "$72,000"],
                ["M3: general availability", "1 June 2027", "$72,000"],
            ],
        ),
        ("h", "Contacts"),
        "Helix: Mei Chen, Director of Partnerships. Northwind: the Head of Platform.",
        ("h", "Data handling"),
        "Helix data, including model outputs and evaluation sets, is governed by the Helix Security "
        "Handbook for everyone working on Project Atlas, in addition to each party's own policies.",
    ],
)

# ───────────────────────── A chat attachment: a vendor's quote ─────────────────────────

docx(
    OUT / "attach" / "acme-cloud-backup-quote.docx",
    "Quote: Acme Cloud Backup for Northwind Robotics",
    [
        "Quote number Q-88213, valid until 30 November 2026.",
        "Service: managed off-site backups of Northwind's ClickHouse telemetry database and PostgreSQL "
        "fleet-state database, including customer telemetry.",
        "Price: $36,000 per year, billed annually in advance.",
        "Term: one year, renewing automatically for successive one-year terms. Notice of non-renewal "
        "must be given at least 120 days before the renewal date.",
        "Security: Acme holds a SOC 2 Type I report dated May 2026. A SOC 2 Type II audit is in "
        "progress and expected in Q3 2027.",
    ],
)

# PDF of the security policy, converted by LibreOffice like a real export.
subprocess.run(
    [
        "soffice",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(NW / "handbook"),
        str(NW / "handbook" / "information-security-policy.docx"),
    ],
    check=True,
    capture_output=True,
)
(NW / "handbook" / "information-security-policy.docx").unlink()
print("fixtures:", sum(1 for p in OUT.rglob("*") if p.is_file()))
