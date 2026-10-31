# Cycle 5 pitch review notes

**Date:** Tuesday, September 8, 2026 (moved from Monday for Labor Day)
**Attendees:** DS, MA, LZ, PR, OA, HV, NH, CO, RS
**Notes:** Noor

## Context

Cycle 5 runs September 14 to October 23, cool-down October 26 to November 6. Per the handbook we pick at most three big bets. Seven pitches came in.

## Big bets (picked)

1. **Tributary** — streaming joins (the Enrich step). Pitched by Mateo and LZ. Scope this cycle: CDC-backed snapshots, the editor preview, dead-letter routing for unmatched events, private beta with the design partners in October. GA target stays January 2027. LZ flagged that snapshot rebuilds after a runner restart are too slow at 50M rows; she will write an RFC on persisting snapshots.
2. **Lantern** — the self-serve billing portal: customers see invoices, change plans, update payment methods and download usage reports without emailing support. Pitched by NH and HV. Target: beta in December, GA January 2027. Depends on the payment-provider migration finishing first.
3. **Hydrant** — automatic scaling for ingest gateways per cell, so nobody runs `nwctl scale ingest` by hand at 3am. Pitched by OA. Includes the gateway TLS session ticket change from INC-2291 as a small batch item.

## Small batch

- Reduce gateway TLS session ticket lifetime to 30 minutes (INC-2291 action item 3). Owner: ingest folks on DP.
- Snowflake key-pair auth in Conduit.
- Flag cleanup: 23 flags over 90 days old.

## Not picked

- Postgres CDC v2 (re-pitch next cycle with a smaller scope).
- Stream-to-stream joins — explicitly out of scope until Tributary is GA.
- Usage alerts by SMS — WX to re-pitch with email only.
- Dark mode for the pipeline editor. Sorry, CO.

## Decisions and follow-ups

- RS to make sure Beacon's hourly counts are exposed to Lantern so the portal can show usage without querying Ledger.
- DS reminded everyone that cool-down is protected: no customer commitments in it, including beta onboarding calls.
- Next pitch review: Monday, November 9.
