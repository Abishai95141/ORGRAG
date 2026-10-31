# Northwind support SLA tiers (2025 edition)

**Owner:** Support Operations · **Effective:** January 1, 2025 · **Applies to:** all customer support tickets

This page sets the response targets that Support commits to for each plan. It covers how fast we reply to a ticket and how often we update it. It does not cover platform availability: the 99.95% availability commitment for Enterprise lives in the Enterprise master agreement.

## Priority definitions

- **P1 — Critical.** Production pipelines are down, or events are not being ingested, for the customer's workspace.
- **P2 — High.** A production pipeline is degraded (late, partially failing) and there is no reasonable workaround.
- **P3 — Normal.** A bug or question with a workaround available.
- **P4 — Low.** Feature requests, documentation questions, account housekeeping.

The customer picks a priority when opening the ticket. Support may change it after triage and must tell the customer why.

## Response targets

| Plan | Priority | First response | Update cadence | Coverage |
|---|---|---|---|---|
| Starter | All priorities | 2 business days | As needed | Business hours, email only |
| Growth | P1 | 4 hours | Every 8 hours | 24/7 |
| Growth | P2 | 8 business hours | Daily | Business hours |
| Growth | P3 and P4 | 2 business days | As needed | Business hours |
| Enterprise | P1 | 1 hour | Every 2 hours | 24/7 |
| Enterprise | P2 | 4 hours | Every 8 hours | 24/7 |
| Enterprise | P3 | 1 business day | Every 2 business days | Business hours |
| Enterprise | P4 | 2 business days | As needed | Business hours |

"Business hours" means 09:00–17:00 Monday to Friday in the customer's local time zone, excluding public holidays in that region.

## How tickets come in

Customers email support@northwind.example or use the help center. Enterprise customers may also page Support for P1 issues through the phone number in their onboarding pack. All tickets are tracked in the helpdesk tool; Slack Connect channels are not a support channel and nothing posted there starts an SLA clock.

## When we miss a target

Missed P1 and P2 first-response targets for Enterprise customers are reviewed weekly by Support Operations. There are no service credits for missed response targets under this edition.

## Escalating to engineering

If a P1 looks like it affects more than one workspace, the support engineer declares an incident with `/incident declare` rather than waiting for engineering to notice. Severity is set by the incident process, not by the ticket priority.
