# Northwind deploy pipeline changes (October 2026)

**From:** Oluwaseun Adeyemi (PLAT) and Mei Tanaka (OBS) · **Posted:** October 9, 2026 in `#eng-announce`
**Effective:** deploys started on or after Monday, October 12, 2026

## Summary

We're making canary analysis slower and stricter. This updates the "Deploys" section of the engineering handbook; where they differ, this note applies, and the handbook will be edited to match.

## What changes

1. **Canary bake time goes from 20 minutes to 45 minutes.** A deploy now sits in the canary cell for 45 minutes before it can be promoted automatically. INC-2291 showed that some failures only show up after caches or sessions expire, and 20 minutes was too short to catch them.
2. **Two new promotion checks.** As well as error rate and p95 latency staying within 10% of the baseline (unchanged), promotion now requires:
   - zero sustained TLS handshake errors between gateways and brokers in canary, and
   - runner consumer lag not growing by more than 2 minutes during the bake.
3. **Data-plane deploys roll out in waves.** Changes under `ingest/` or `runner/` no longer go to every cell at once after canary. They go canary → `us-central` → `us-west` → `us-east` → `eu-central`, with 30 minutes between waves. Other services still go to all cells at once after canary.
4. **Holding a promotion.** Anyone can type `/deploy hold <deploy-id>` in Slack to stop a deploy from promoting; it stays in canary until someone runs `/deploy release` or rolls it back. Use it if something looks odd and you need time to check.
5. **New status command.** `nwctl deploy status` shows where each in-flight deploy is, which checks it has passed, and how long until the next wave.

## What doesn't change

- The deploy window is still Monday through Thursday, 09:00–16:00 Pacific.
- Friday deploys still need approval from the on-call engineering manager.
- Rollbacks are still allowed at any time, without approval, including during freezes.
- Two approvals for changes to billing, authentication and the data plane, per the handbook's code review rules.

## Impact on lead time

A typical non-data-plane change now reaches all production cells about 50 minutes after merge instead of about 25. A data-plane change takes about two and a half hours to reach every cell. If you're shipping a fix during an incident, the incident commander can approve skipping the bake with `/deploy expedite`; the expedite is logged in `#eng-changes`.

## Dashboards

Mei has added a "Canary analysis" panel to Lookout showing each check's value against its threshold for the current canary deploy.

Questions in `#team-platform`.
