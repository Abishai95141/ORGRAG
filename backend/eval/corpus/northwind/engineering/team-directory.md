# Engineering team directory and change approvers

**Owner:** Dmitri Sokolov, VP Engineering · **Updated:** September 30, 2026

Team codes are used in the service catalog, CODEOWNERS, PagerDuty schedules and the issue tracker. Each team has one named change approver and one delegate who can approve when the approver is out. Approvers sign off on "normal" production changes as described in the production change approval policy.

## Teams

| Code | Team | Engineering manager | Change approver | Delegate | Slack |
|---|---|---|---|---|---|
| PLAT | Platform | Oluwaseun Adeyemi | Oluwaseun Adeyemi | Kofi Mensah | `#team-platform` |
| DP | Data Plane | Priya Raman | Priya Raman | Lin Zhou | `#team-dataplane` |
| IDB | Identity & Billing | Harriet Vance | Harriet Vance | Arjun Mehta | `#team-idb` |
| WX | Workspace Experience | Camille Ortega | Camille Ortega | Theo Lindqvist | `#team-wx` |
| OBS | Observability | Ravi Subramanian | Ravi Subramanian | Mei Tanaka | `#team-obs` |
| SEC | Security | Tomasz Wierzbicki (Head of Security) | Tomasz Wierzbicki | Security on-call | `#security-help` |

Most people go by first names in Slack: Oluwaseun is "Seun", and initials are common in reviews and RFCs (OA, PR, HV, CO, RS, TW, LZ).

## Who's who beyond the managers

- **Lin Zhou** (DP) — staff engineer, tech lead for streaming joins.
- **Kofi Mensah** (PLAT) — senior SRE, on the incident commander rotation.
- **Arjun Mehta** (IDB) — senior engineer, owns the invoicing jobs.
- **Mei Tanaka** (OBS) — maintains the ingest and runner dashboards.
- **Theo Lindqvist** (WX) — tech lead for workspace settings and notifications.

## Leadership

- Dmitri Sokolov — VP Engineering. Reports to the CEO.
- Mateo Alvarez — Group PM, data products (works with DP).
- Noor Haddad — PM, billing and accounts (works with IDB).

## Incident commander rotation

The IC rotation is separate from team on-call. It currently includes Kofi Mensah, Priya Raman, Harriet Vance, Oluwaseun Adeyemi and Ravi Subramanian. ICs serve one week at a time and are paged by `/incident declare`.

## Changes to this page

Managers update their own rows. New teams need a code that does not collide with an existing CODEOWNERS group; ask in `#eng-leads`.
