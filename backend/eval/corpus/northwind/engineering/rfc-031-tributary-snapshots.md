# RFC-031: Persisting Tributary snapshots

**Author:** L. Zhou · **Created:** September 18, 2026 · **Status:** Accepted with conditions
**Reviewers:** PR (Data Plane), OA (Platform), TW (Security)

## Approvals

| Reviewer | Decision | Date | Notes |
|---|---|---|---|
| PR | ✅ Approve | Sep 22 | |
| OA | ✅ Approve with conditions | Sep 24 | Snapshots go in a separate Driftwood bucket, not the event archive buckets; PLAT owns the bucket and its lifecycle rules |
| TW | ⏳ Pending | — | Wants a threat model for copying Restricted data across tiers before approving |

Implementation can start behind a flag, but nothing ships to the private beta until TW signs off.

## Problem

Today a runner rebuilds its reference-table snapshot from scratch (full table scan plus CDC catch-up) whenever it restarts or CDC lag exceeds 10 minutes. For a 50M-row table that takes about 25 minutes, during which Enrich steps on that partition stall. Deploys restart runners several times a day, so beta partners would see regular enrichment gaps.

## Proposal

1. Every 15 minutes each runner partition writes a compacted snapshot of its RocksDB reference state to object storage in Driftwood, tagged with the CDC log position.
2. On restart, the runner downloads the latest snapshot and replays CDC from the recorded position instead of rebuilding.
3. Snapshots are encrypted with a per-workspace data key. Old snapshots are deleted 7 days after being replaced (this matches the retention standard v2 entry).

Target restore time: under 90 seconds for a 50M-row table.

## Memory and storage budget

- Per-partition memory budget for reference state stays at 2 GiB. Partitions that would exceed it spill to disk rather than failing.
- Expected snapshot storage across all beta workspaces: about 4 TB.

## Alternatives considered

- **Keep full rebuilds, just make them faster.** Parallel scans get us to maybe 12 minutes. Still too slow.
- **Shared snapshot service.** A central cache all runners read from. More moving parts, and a new datastore would need its own ADR.
- **Replicate state between runner pods.** Doubles memory cost.

## Open questions

- Should snapshot writes pause during the month-end billing freeze? (Probably not — this isn't Ledger.)
- Does persisting reference data change our subprocessor disclosures? Asked legal.

## Rollout

Flag `tributary-persisted-snapshots`, canary cell first, then the beta workspaces.
