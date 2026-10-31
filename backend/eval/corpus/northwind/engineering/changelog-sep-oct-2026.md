# Northwind Pipelines changelog: September–October 2026

Customer-visible changes and notable internal fixes, newest first. Internal-only entries are marked *(internal)*.

## 2026.10.3 — October 20

- **Audit log export.** Workspace admins can export the audit log as CSV from Settings → Security.
- **Deprecation notice.** The legacy `/v1/track` ingest endpoint will stop accepting events on April 30, 2027. SDKs released after March 2025 already use `/v2/events`.
- Fixed: the pipeline editor sometimes showed stale schema previews after a source was renamed.

## 2026.10.2 — October 8

- *(internal)* Ledger: invoice creation is now idempotent per workspace and billing period, preventing the duplicate invoices seen in INC-2340.
- Billing emails now include a link to the usage breakdown for the period.

## 2026.10.1 — October 1

- **Enrich step (private beta).** Streaming joins are available to private beta design partners. Each enriched event counts as 1.2 events against quota.
- Unmatched events from an Enrich step can be routed to a dead-letter destination.

## 2026.09.3 — September 24

- **Snowflake key-pair authentication.** Snowflake sources and destinations in Conduit now support key-pair auth in addition to passwords. Password auth for Snowflake will be removed in 2027.
- **Web app session timeout** reduced from 12 hours to 8 hours of inactivity for all workspaces. Admins on Enterprise can set a shorter timeout.

## 2026.09.2 — September 12

- *(internal)* Ingest gateways: TLS session ticket lifetime reduced from 4 hours to 30 minutes, so certificate problems surface quickly instead of hours later (INC-2291 action item 3). Shipped ahead of the September 15 due date.
- *(internal)* Ingest dashboard: added panels for p99 gateway latency by cell.

## 2026.09.1 — September 3

- Postgres sources: support for logical replication slots on read replicas.
- Fixed: retries from the Python SDK could reorder events within a batch when the gateway returned 503.
- *(internal)* Beacon: hourly usage counts are now kept for 25 months instead of 13.
