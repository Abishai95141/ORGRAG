# Hydrant launch notes: ingest gateway autoscaling

**Author:** Oluwaseun Adeyemi (PLAT) · **Date:** October 22, 2026 · **Status:** Shipped to all production cells

Hydrant, one of the cycle 5 big bets, is live. It scales `ingest-gateway` replicas in each cell automatically, so nobody has to scale gateways by hand in the middle of the night.

## What it does

Hydrant watches each cell's gateways and adjusts the replica count between that cell's floor and ceiling. The floor and ceiling for every cell are listed in the production cell inventory.

- **Scale up** when average gateway CPU stays above 65% for 3 minutes, or when Kafka produce latency p95 stays above 250 ms for 3 minutes. Each step adds 25% of the current replicas (at least 2).
- **Scale down** when average CPU stays below 35% for 20 minutes. Hydrant removes one replica every 5 minutes, never going below the floor.
- Hydrant never scales during the first 10 minutes after a gateway deploy, so canary analysis isn't disturbed.

## Rollout

| Cell | Enabled |
|---|---|
| canary | October 13 |
| us-west | October 15 |
| us-central | October 19 |
| us-east | October 21 |
| eu-central | October 22 |

## What changes for on-call

**This replaces the gateway scaling step in the on-call runbook.** Do not run `nwctl scale ingest` by hand any more: Hydrant treats the replica count as its own and will revert a manual change within 2 minutes.

If you need to pin the count, for example while debugging a bad gateway build, use a hold:

```
nwctl hydrant hold --cell <cell> --replicas <n> --ttl 2h
```

A hold can last at most **4 hours**, after which Hydrant takes over again. Holds are standard changes and need no approval. Release a hold early with `nwctl hydrant release --cell <cell>`.

The rest of the "Ingest 5xx rate above 1%" steps in the runbook still apply: check which cell is affected and look at gateway TLS handshake errors first.

## Alerts

- **Hydrant at ceiling:** if a cell sits at its ceiling for 10 minutes, Hydrant pages the **Data Plane primary on-call**, because hitting the ceiling usually means a gateway problem rather than real traffic.
- **Hydrant controller down:** pages the Platform primary. Gateways keep their last replica count while the controller is down.

## Who owns what

Hydrant itself is owned by PLAT; the gateways remain owned by DP. Hydrant's configuration, including each cell's floor and ceiling, lives in the PLAT infrastructure repo. Raising or lowering a floor or ceiling is a **normal change** (infrastructure configuration) and needs sign-off from **PLAT's change approver**. Hydrant stores no customer data and is not a Restricted service, so no security sign-off is needed for these changes.

## Known issues

- In eu-central, Hydrant briefly oscillated between two replica counts on launch day (INC-2358, no customer impact). The scale-down cool-off has been raised there while we look into it.
- Hydrant doesn't scale runners. Runner scaling is a separate pitch.

Questions in `#team-platform`.
