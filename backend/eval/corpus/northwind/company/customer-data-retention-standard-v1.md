# Customer data retention standard (v1)

**Version:** 1.0 · **Approved:** February 2, 2026 · **Owner:** Security team

This standard sets how long Northwind keeps data that customers send us, and how we delete it.

## Retention periods

| Data | Where it lives | Default retention |
|---|---|---|
| Event payloads in the hot buffer | Kafka (per cell) | 7 days |
| Archived event payloads | Cold store (object storage) | 400 days |
| Pipeline run logs | Lookout | 90 days |
| Database backups | Managed Postgres snapshots | 35 days |
| Workspace metadata | Atlas | Life of the account + 30 days |

Enterprise customers may contract for a shorter archive period. Longer periods are not offered in v1.

## Deletion requests

When a customer asks us to delete their data (for example under GDPR), we complete deletion from all live systems within **30 days** of the verified request. Backups age out on their normal 35-day schedule and are never restored for the purpose of reading deleted data.

## Account closure

When a workspace is closed, event data in the hot buffer and cold store is deleted within 30 days, and workspace metadata is deleted 30 days after closure.

## Responsibilities

- The Security team owns this standard and reviews it twice a year.
- The owning team of each system implements deletion; the cold store is deleted by lifecycle rules.
- Support must log every deletion request in the `data-requests` queue.

## Questions

Ask in `#security-help`.
