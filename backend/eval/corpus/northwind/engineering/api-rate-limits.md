# Northwind Pipelines API rate limits by plan

**Owner:** Data Plane (DP) for ingest endpoints, Workspace Experience (WX) for the management API · **Last updated:** October 21, 2026

Rate limits protect the shared ingest gateways and the management API. This page is the internal source of truth; the public docs copy it on each release.

## How limits work

- Limits are **per workspace**, not per API key. Ten keys in one workspace share one budget.
- Short bursts of up to 2x the limit are allowed for 10 seconds.
- Over the limit, the API returns **HTTP 429** with a `Retry-After` header giving the number of seconds to wait. Our SDKs honour it automatically and back off exponentially.
- Rejected requests are not counted against the monthly event quota.

## Limits

| Endpoint | Used for | Starter | Growth | Enterprise (default) | Max per request |
|---|---|---|---|---|---|
| `POST /v2/events` | Event ingest from SDKs and the HTTP API | 100 req/s | 2,000 req/s | 10,000 req/s | 500 events or 1 MB |
| `POST /v2/events/batch` | Bulk backfill and replays | Not available | 50 req/s | 200 req/s | 10,000 events or 10 MB |
| `POST /v1/track` (legacy) | Old ingest endpoint; stops accepting events April 30, 2027 | 50 req/s | 500 req/s | 1,000 req/s | 100 events |
| `GET /v2/pipelines` | List and read pipelines | 10 req/s | 50 req/s | 100 req/s | — |
| `POST /v2/pipelines/{id}/runs` | Trigger a pipeline run | 1 req/s | 10 req/s | 25 req/s | — |
| `GET /v2/usage` | Usage counts (served from Beacon) | 2 req/s | 10 req/s | 20 req/s | — |
| `POST /v2/reference-tables/{id}/refresh` | Force an Enrich snapshot rebuild | Not available | 1 per 10 minutes | 1 per 2 minutes | — |
| `GET /v2/audit-log/export` | Audit log CSV export | Not available | 1 per hour | 6 per hour | — |
| `/v2/scim/*` | SCIM user provisioning | Not available | Not available | 20 req/s | — |

Endpoints marked "Not available" return HTTP 403 on that plan, not 429.

## Raising limits

- **Enterprise:** ingest limits on `/v2/events` and `/v2/events/batch` can be raised up to 3x the default by contract. The account team files a request in the DP queue; DP checks the customer's cell has gateway headroom before agreeing.
- **Growth:** limits are fixed by contract; apart from the one-off backfill raises below, customers who need more should talk to sales about Enterprise.
- Temporary raises for a one-off backfill (up to 72 hours), on any plan that has the endpoint, can be granted by the DP on-call primary.

## Legacy endpoint

`/v1/track` keeps its lower limits until it is switched off. We will not raise them for anyone; customers should move to `/v2/events`, which every SDK released after March 2025 already uses.

## Why reference-table refreshes are so limited

A forced refresh throws away the runner's snapshot and rebuilds it from a full table scan, which is expensive for large tables. Most customers never need it: snapshots are kept current with change data capture.
