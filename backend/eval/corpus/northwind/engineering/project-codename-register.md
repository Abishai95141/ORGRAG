# Northwind project codename register

**Maintained by:** Mateo Alvarez (Group PM) · **Last updated:** October 28, 2026

Codenames are for internal use only and must never appear in customer-facing material, release notes or the status page. This register maps each codename to what it actually is. Request a new codename in `#eng-leads`. It must not collide with a production service name, a vendor name or an existing codename.

## Active and recent codenames

| Codename | What it is | Owning team | Lead | Status (Oct 28) |
|---|---|---|---|---|
| Tributary | Streaming joins (the Enrich step in the pipeline editor) | DP | Lin Zhou (LZ) | Private beta since October 1; GA January 2027 |
| Lantern | Self-serve billing portal | IDB | Noor Haddad (NH) | Beta February 2027, GA March 2027 |
| Hydrant | Automatic scaling of ingest gateways per cell | PLAT | Oluwaseun Adeyemi (OA) | Shipped October 22, 2026 |
| Undertow | The payment-provider migration: moving card collection and Ledger's customer tokens to the new payment provider | IDB | Arjun Mehta (AM) | In progress |
| Sandbar | EU data residency: building and running the `eu-central` cell | PLAT | Oluwaseun Adeyemi (OA) | Limited availability |
| Tidepool | Postgres CDC v2 | DP | Priya Raman (PR) | Not picked in cycle 5; re-pitch with smaller scope |
| Bellbuoy | Usage alerts by email (the SMS version was dropped) | WX | Theo Lindqvist (TL) | Re-pitch for cycle 6 |
| Wayfinder | Pipeline editor redesign | WX | Camille Ortega (CO) | Discovery |

Status lives in each project's own updates; this column is only a snapshot.

## Retired codenames

| Codename | What it was | Retired |
|---|---|---|
| Keel | Original multi-cell architecture | 2022 |
| Gannet | Migration of runners to Graviton instances | Q3 2026 |

## Nicknames you'll hear

These are not codenames, just informal names for production services that turn up in Slack and incident channels:

- **"the firehose"** — `ingest-gateway`.
- **"the attic"** — Driftwood, the long-term event archive (older documents call it the cold store).
- **"the meter"** — Beacon, usage metering.
- **"the till"** — Ledger.

When writing anything that will outlive the conversation (runbooks, postmortems, tickets), use the real service name.

## Common mix-ups

- **Sandbar** is not **Saltmarsh**. Saltmarsh Analytics is a vendor; Sandbar is our EU cell project.
- **Tidepool** is not **Tributary**. Tidepool is CDC v2, Tributary is streaming joins.
- **Driftwood** is a production service, not a project codename, so it isn't in the table.
