# Service catalog

**Maintained by:** Platform team · **Last updated:** October 2, 2026

This is the source of truth for which production service does what, what data it keeps, and which team owns it. Owning teams are listed by their short code; see the engineering team directory for who is behind each code. If you are adding a new datastore, remember an ADR is required before the service appears here.

## Production services

| Service | What it does | Data it stores | Data class | Owner |
|---|---|---|---|---|
| `ingest-gateway` | Accepts events from customer SDKs and the HTTP API, authenticates them and writes them to Kafka | In-flight event payloads only; nothing persisted beyond the Kafka hot buffer | Restricted | DP |
| `runner` | Executes customer pipelines: transforms, Enrich steps, destinations | Partition state in local RocksDB, reference-table snapshots | Restricted | DP |
| Conduit | Source and destination connectors (Postgres, MySQL, Snowflake, warehouses) | Encrypted connector credentials, sync cursors | Restricted | DP |
| Driftwood | Long-term object archive behind the hot buffer | Archived event payloads, partitioned by workspace and day | Restricted | PLAT |
| Keystone | Authentication and authorization for the app and API | Hashed API keys, SSO/SAML configuration, session tokens | Restricted | IDB |
| Ledger | Plans, invoicing and payment collection | Invoices, plan history, monthly usage totals, payment-provider customer tokens | Confidential | IDB |
| Atlas | Workspace and account metadata | Workspace settings, customer user names, email addresses and roles | Confidential | WX |
| Beacon | Usage metering and quota enforcement | Per-workspace event counts by hour | Internal | OBS |
| Lookout | Internal dashboards and alert routing | Metrics, traces, alert history | Internal | OBS |

## Shared infrastructure

| Component | Owner | Notes |
|---|---|---|
| Kafka clusters (one per cell) | PLAT | Hot buffer; 7-day topic retention |
| Kubernetes clusters | PLAT | One per cell plus the canary cell |
| Managed Postgres | PLAT | Backs Keystone, Ledger and Atlas; DP owns schemas for Conduit |
| Teleport | SEC | Production access, 8-hour sessions |

## Notes

- "Restricted" and "Confidential" follow the data classification table in the information security policy.
- Driftwood was previously called the "cold store" in older runbooks; it is the same object-storage archive.
- Ledger never stores card numbers. Card data lives only with the payment provider; Ledger keeps an opaque customer token.
- Beacon counts are what quota enforcement and overage billing use. Ledger reads monthly totals from Beacon on the first of each month.

To change an owner, open a PR against this page and tag both the old and new engineering managers.
