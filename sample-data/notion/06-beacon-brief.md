# Beacon AI pilot: brief

Product manager: Jonah Price. Sponsor: Ruth Adeyemi.

## What it is

An internal assistant that answers staff questions from our handbook, standards, decision log and Slack, with citations. It drafts; a person decides. Beacon never sends a message or changes a record on its own, which is how we meet principle 4 of the AI Playbook (meaningful human control).

## Pilot

- 40 staff for 8 weeks, from Monday 3 November 2026.
- Success: 20 minutes saved per person per day, and at least 80% of answers rated correct in a weekly sample of 50.
- For comparison, the cross-government M365 Copilot experiment reported an average of 26 minutes saved a day.

## Platform

| Option | Cost | Notes |
| --- | --- | --- |
| Lighthouse (SaaS) | £38,000 a year for 40 seats | Data leaves our account |
| Keel (open source, self-hosted) | About £9,000 a year in hosting | Chosen on 14 October (ADR-008) |
| Build our own | About 2 developers for 3 months | Rejected: no capacity before beta |

## Assurance

- Algorithmic Transparency Recording Standard record: Aisha Bello. Draft on 29 October, signed off on 31 October.
- Red team: 0 of 25 prompt-injection attempts leaked restricted data.
- Data protection impact assessment: approved on 22 October.

## Risks

- Answers citing superseded policies. Mitigation: mark superseded pages and prefer the newest.
- Low adoption. Mitigation: weekly tips in #general.
