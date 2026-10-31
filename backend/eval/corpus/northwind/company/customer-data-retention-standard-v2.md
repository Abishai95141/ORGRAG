# Customer data retention standard (v2)

**Version:** 2.0 · **Effective:** September 15, 2026 · **Owner:** Security team (Tomasz Wierzbicki)
**Supersedes:** Customer data retention standard v1.0 (February 2, 2026). Where the two differ, this version applies.

## What changed from v1

- Default archive retention is reduced from 400 days to **365 days**.
- Deletion requests must now be completed within **14 days** instead of 30.
- Enterprise customers can now contract for *longer* archive retention, up to 730 days, with approval from the security team.
- The cold store is now referred to by its service name, Driftwood.

## Retention periods

| Data | Where it lives | Default retention |
|---|---|---|
| Event payloads in the hot buffer | Kafka (per cell) | 7 days |
| Archived event payloads | Driftwood | 365 days |
| Reference-table snapshots | Runner local state / Driftwood | 7 days after the snapshot is replaced |
| Pipeline run logs | Lookout | 90 days |
| Database backups | Managed Postgres snapshots | 35 days |
| Workspace metadata | Atlas | Life of the account + 30 days |

## How deletion is enforced

Archive retention is enforced by object-storage lifecycle rules on the Driftwood buckets, which delete objects once they pass the retention period for their workspace. Per-workspace overrides are stored as bucket tags. Changing these lifecycle rules is a normal change under the production change approval policy.

## Deletion requests

Verified deletion requests (for example GDPR erasure) are completed in all live systems within **14 days**. Backups still age out on the 35-day schedule. Support logs every request in the `data-requests` queue; the queue alerts at day 10.

## Account closure

When a workspace is closed, event data in the hot buffer and Driftwood is deleted within 14 days, and workspace metadata 30 days after closure.

## Review

The Security team reviews this standard every six months. The next review is due March 2027.
