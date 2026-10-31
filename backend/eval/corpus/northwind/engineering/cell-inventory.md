# Northwind production cell inventory

**Maintained by:** Platform team (PLAT) · **Last updated:** October 23, 2026

A cell is a self-contained copy of the Northwind Pipelines data plane: its own Kubernetes cluster, Kafka cluster, ingest gateways and runners. A workspace lives in exactly one cell, chosen when the workspace is created, and its event data does not leave that cell's region. The service catalog lists which team owns each service; this page lists the cells themselves.

## Cells

| Cell | AWS region | Location | Status | Opened | Data residency | Workspaces (Oct 1, 2026) | Kafka brokers | Gateway replicas (Hydrant floor / ceiling) |
|---|---|---|---|---|---|---|---|---|
| `us-east` | us-east-1 | N. Virginia | Production | March 2021 | US | 6,240 | 12 | 16 / 64 |
| `us-central` | us-east-2 | Ohio | Production | June 2024 | US | 2,980 | 6 | 8 / 40 |
| `us-west` | us-west-2 | Oregon | Production | September 2022 | US | 3,410 | 9 | 10 / 48 |
| `eu-central` | eu-central-1 | Frankfurt | Limited availability | September 21, 2026 | EU | 146 | 6 | 4 / 24 |
| `eu-west` | eu-west-1 | Ireland | Planned, target Q2 2027 | — | EU | — | TBD | TBD |
| `canary` | us-east-1 | N. Virginia | Canary (5% of traffic) | March 2021 | US | — (traffic split) | 3 | 2 / 8 |
| `staging` | us-west-2 | Oregon | Pre-production | 2021 | Synthetic data only | — | 3 | 2 / 6 |

Note the naming: the `us-central` cell runs in AWS region **us-east-2** (Ohio). It is named for the network latency zone it serves, not for the AWS region name. Do not confuse it with the `us-east` cell.

## Notes per cell

- **us-east** is the oldest and largest cell. It was the cell affected by INC-2291 in August 2026.
- **us-central** was opened to take new workspaces when us-east passed 6,000 workspaces. New US workspaces are placed in us-central or us-west by default.
- **eu-central** accepts new workspaces from EU customers who ask for EU data residency. Moving an existing workspace from a US cell is a manual migration run by PLAT and needs a change ticket. Application logs from eu-central are kept in Lookout in-region and are not shipped to Cobaltline, which is US-only.
- **eu-west** is planned as a second EU cell; broker counts and gateway limits will be set when capacity planning is done.
- **canary** receives every deploy first. It holds no workspaces of its own; a share of live traffic is routed to it.
- **staging** never holds customer data.

## Changing this page

Gateway floors and ceilings are Hydrant configuration. Changing them is a normal change under the production change approval policy; see the Hydrant launch notes. Broker counts change only through capacity planning with PLAT.

To request a new cell, open a ticket in the PLAT queue with the expected workspace count and residency requirements.
