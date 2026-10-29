# Postmortem: Ingest outage in us-east cell (INC-2291)

**Date of incident:** August 14, 2026
**Duration:** 2 hours 47 minutes (14:05–16:52 UTC)
**Severity:** SEV-1
**Author:** Priya Raman, with the ingest team

## Summary

Customers in the us-east cell were unable to push new events into Northwind Pipelines for 2 hours 47 minutes. Roughly 1,140 workspaces were affected. No data that was successfully acknowledged was lost, but about 38 million events were rejected with HTTP 503 and had to be retried by customer SDKs.

## Impact

- 1,140 workspaces saw failed writes; 212 of them are on Enterprise plans.
- Pipelines that depended on fresh data ran late; the median delay was 3 hours 10 minutes.
- Support received 96 tickets. We issued SLA credits to 41 Enterprise customers under the 99.95% availability commitment.

## Root cause

A routine certificate rotation on the internal Kafka brokers shipped a certificate bundle that was missing the intermediate CA. The ingest gateways cached the old TLS session tickets, so they kept working until the session ticket lifetime (4 hours) expired. When tickets expired at 14:05, every gateway in the cell failed its TLS handshake with the brokers at once.

The canary cell did not catch this because it had been restarted after the rotation and its gateways failed immediately, but the alert threshold for the canary was set to page only above 50 failed handshakes per minute; the canary handled too little traffic to cross it.

## Timeline (UTC)

- 10:02 — Certificate rotation job completes on us-east brokers.
- 14:05 — Gateway TLS session tickets expire; handshake failures spike.
- 14:09 — Error-rate alert pages the on-call ingest engineer.
- 14:31 — Incident declared SEV-1; incident commander assigned.
- 15:40 — Missing intermediate certificate identified.
- 16:20 — Corrected bundle deployed to brokers.
- 16:52 — Write success rate back above 99.9%; incident resolved.

## What went well

The customer SDKs' retry with exponential backoff meant most events were eventually delivered without customer action. Status page updates went out every 30 minutes.

## What went poorly

It took 89 minutes from the first page to find the cause, mostly because broker logs showed no errors — the failure was entirely on the client side of the handshake.

## Action items

1. Validate certificate chains in the rotation job before publishing (owner: platform team, due Aug 28) — **done**.
2. Lower the canary handshake-failure alert to page on any sustained failures over 5 minutes (owner: ingest team) — **done**.
3. Reduce gateway TLS session ticket lifetime from 4 hours to 30 minutes so problems surface sooner (owner: ingest team, due Sep 15).
4. Add a "TLS handshake errors" panel to the ingest dashboard (owner: observability) — **done**.
