# On-call runbook

Northwind runs a follow-the-sun on-call rotation across two teams: Platform (infrastructure, Kafka, Kubernetes) and Data Plane (ingest gateways, pipeline runners, connectors).

## Rotation

- Shifts are one week long, starting Tuesday at 10:00 local time.
- Each shift has a primary and a secondary. The secondary is paged if the primary does not acknowledge within 10 minutes.
- On-call engineers receive a stipend of $400 per week, plus $100 for each page outside business hours.
- Swaps are arranged in `#oncall-swaps` and must be reflected in PagerDuty before the shift starts.

## Severity levels

| Level | Definition | Response |
|---|---|---|
| SEV-1 | Data loss, or a full outage of ingest or pipelines for a cell | Page immediately, declare incident, IC assigned within 15 minutes |
| SEV-2 | Partial outage or degraded performance affecting many customers | Page, acknowledge within 15 minutes |
| SEV-3 | Single-customer issue or degraded non-critical feature | Ticket, next business day |

## Declaring an incident

Use `/incident declare` in Slack. This opens a channel, starts the timeline, and pages the incident commander rotation. For SEV-1 incidents, the communications lead posts the first status page update within 20 minutes.

## Common alerts

**Ingest 5xx rate above 1%.** Check the ingest dashboard for which cell is affected. If the brokers are healthy, look at gateway TLS handshake errors (see INC-2291). Scale gateways with `nwctl scale ingest --cell <cell> --replicas +4` if CPU is above 80%.

**Pipeline lag above 15 minutes.** Usually a stuck partition. Run `nwctl runners lag --cell <cell>` to find it, then restart the runner pod that owns the partition.

**Connector auth failures spike.** Often a customer rotated credentials. If more than ten workspaces are affected at once, suspect an upstream API change and escalate to the Data Plane lead.

## After the incident

Every SEV-1 and SEV-2 gets a blameless postmortem within five business days, reviewed at the Thursday reliability meeting.
