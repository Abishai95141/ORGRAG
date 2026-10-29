# Product spec: Streaming joins

**Status:** Approved for the Q4 cycle
**Owner:** Mateo Alvarez (PM), Lin Zhou (tech lead)

## Problem

Customers increasingly want to enrich event streams with reference data — for example, joining a `checkout_completed` event with the latest row from a `customers` table — before the data lands in their warehouse. Today they do this downstream in SQL, which delays insights by the length of the warehouse load interval (typically 15–60 minutes) and costs them warehouse compute.

## Goals

- Let users define a join between one event stream and one keyed reference table in the pipeline editor.
- Enrichment adds less than 150 ms to end-to-end p95 latency.
- Support reference tables up to 50 million rows per workspace on the Growth plan and 500 million on Enterprise.

## Non-goals

- Stream-to-stream joins with time windows. These are planned for a later release.
- Joins against tables that change more than 1,000 times per second.

## User experience

In the pipeline editor, a new **Enrich** step sits between **Source** and **Transform**. The user picks a reference table (from a connected Postgres, MySQL or Snowflake source), the key column, and the event field to match on. A preview shows ten recent events with the joined columns highlighted. Unmatched events can either pass through with nulls (default) or be routed to a dead-letter destination.

## Technical approach

Reference tables are snapshotted into the runner's local RocksDB state and kept current with change data capture. Each runner partition only holds the keys it needs, using the same partitioning as the event stream. Snapshots are rebuilt from scratch if CDC lag exceeds 10 minutes.

## Pricing

Streaming joins are included on Growth and Enterprise. Each enriched event counts as 1.2 events against the monthly quota.

## Launch plan

Private beta with eight design partners in October, public beta in November, general availability targeted for January 2027.
