# Northwind support SLA tiers (effective August 1, 2026)

**Owner:** Support Operations (Gwen Talbot) · **Approved by:** Marcus Bell (CRO) · **Effective:** August 1, 2026
**Replaces:** Northwind support SLA tiers (2025 edition). Tickets opened on or after August 1, 2026 use the targets on this page.

## What changed from the 2025 edition

- Growth P1 first response is now **2 hours** (was 4 hours).
- Enterprise P1 first response is now **30 minutes** (was 1 hour), with hourly updates.
- Enterprise P2 first response is now **2 hours** (was 4 hours).
- New **Premium Support** add-on for Enterprise, with a named technical account manager (TAM).
- Growth P3 and P4 are now separate rows.
- Business hours are now 08:00–18:00 in the customer's support region.
- Service credits apply to missed Enterprise P1 first responses.

## Priority definitions

These are unchanged from the 2025 edition:

- **P1 — Critical.** Production pipelines are down, or events are not being ingested, for the customer's workspace.
- **P2 — High.** A production pipeline is degraded (late, partially failing) with no reasonable workaround.
- **P3 — Normal.** A bug or question with a workaround available.
- **P4 — Low.** Feature requests, documentation questions, account housekeeping.

## Response targets

| Plan | Priority | First response | Update cadence | Coverage |
|---|---|---|---|---|
| Starter | All priorities | 2 business days | As needed | Business hours, email only |
| Growth | P1 | 2 hours | Every 4 hours | 24/7 |
| Growth | P2 | 8 business hours | Daily | Business hours |
| Growth | P3 | 2 business days | Every 3 business days | Business hours |
| Growth | P4 | 3 business days | As needed | Business hours |
| Enterprise | P1 | 30 minutes | Every hour | 24/7 |
| Enterprise | P2 | 2 hours | Every 4 hours | 24/7 |
| Enterprise | P3 | 1 business day | Every 2 business days | Business hours |
| Enterprise | P4 | 2 business days | As needed | Business hours |
| Enterprise + Premium Support | P1 | 15 minutes | Every 30 minutes | 24/7, TAM joins the call |
| Enterprise + Premium Support | P2 | 1 hour | Every 2 hours | 24/7 |
| Enterprise + Premium Support | P3 and P4 | 4 business hours | Daily | Business hours, TAM |

"Business hours" means 08:00–18:00, Monday to Friday, in the customer's support region (US Pacific, US Eastern, or UK), excluding that region's public holidays.

## Premium Support

Premium Support is only sold with Enterprise. It costs $24,000 per year or 10% of the annual contract value, whichever is higher. It includes a named TAM, a quarterly architecture review and the response targets above. It cannot be added to Growth.

## Service credits

If we miss the first-response target on an Enterprise P1 ticket, the customer gets a credit of 5% of that month's fee per miss, capped at 25% per month. Credits are requested by the account team and approved by Support Operations. These are separate from SLA credits for availability under the 99.95% commitment.

## How tickets come in

Tickets are handled in the Hollowpine help center or by email to support@northwind.example. Enterprise customers can page Support for P1 issues by phone. Slack Connect messages do not start an SLA clock. Never ask a customer to paste event payloads into a ticket; ask for field names and counts.

## Escalating to engineering

If a P1 affects more than one workspace, declare an incident with `/incident declare`. Severity is set by the incident process, not by the ticket priority.
