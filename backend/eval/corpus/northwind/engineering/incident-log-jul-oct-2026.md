# Northwind incident log: July–October 2026

**Maintained by:** the reliability meeting (Thursdays) · **Exported:** October 30, 2026 from the incident tracker

Every incident declared with `/incident declare` between July 1 and October 30, 2026. SEV-3 entries are listed when someone declared them; most SEV-3 issues are handled as tickets and never appear here. Durations run from declaration to resolution, except INC-2291, which the tracker records from first customer impact (14:05–16:52 UTC), as in its postmortem. Owning teams are not tracked here; see the service catalog.

## Log

| Incident | Date | Severity | What happened | Service | Cell | Incident commander | Duration | Postmortem |
|---|---|---|---|---|---|---|---|---|
| INC-2264 | Jul 9 | SEV-3 | One workspace's Snowflake destination failing auth after the customer rotated credentials | Conduit | us-west | — | 5h 20m | Not required |
| INC-2278 | Jul 28 | SEV-2 | Archive writes backed up; us-west hot-buffer topics came within 6 hours of the 7-day retention limit | Driftwood | us-west | Kofi Mensah | 9h 40m | Published Aug 4 |
| INC-2291 | Aug 14 | SEV-1 | Ingest outage: broker certificate bundle missing the intermediate CA | ingest-gateway | us-east | Oluwaseun Adeyemi | 2h 47m | Published Aug 21 |
| INC-2302 | Aug 27 | SEV-2 | SAML SSO logins failing for 61 workspaces after an identity-provider metadata refresh | Keystone | All cells | Ravi Subramanian | 1h 35m | Published Sep 3 |
| INC-2315 | Sep 10 | SEV-3 | Internal dashboards blank; alert routing unaffected | Lookout | — | — | 40m | Not required |
| INC-2327 | Sep 29 | SEV-2 | Hourly usage double-counted for 3 hours; 418 workspaces got false quota warnings | Beacon | us-central | Priya Raman | 3h 05m | Published Oct 6 |
| INC-2340 | Oct 6 | SEV-2 | Duplicate September invoices; 201 card charges refunded | Ledger | All cells | Kofi Mensah | 3h 19m | Published Oct 13 |
| INC-2351 | Oct 14 | SEV-2 | Snowflake destinations failing to load for 63 workspaces | Conduit | us-east, us-west | Harriet Vance | 1h 50m | In review |
| INC-2358 | Oct 22 | SEV-3 | Gateway replica count oscillating on Hydrant launch day; no customer impact | Hydrant | eu-central | — | 25m | Not required |
| INC-2366 | Oct 27 | SEV-3 | Invite and password-reset emails delayed about 2 hours behind a webhook backlog | Atlas | — | — | 2h 10m | Not required |

## Notes

- **INC-2291** and **INC-2340** have full write-ups (the INC-2291 postmortem and the INC-2340 Slack export plus postmortem).
- **INC-2351:** the postmortem draft was circulated on October 21 and is due for review at the reliability meeting on November 5. Root cause and action items will be added here once it is accepted. Please don't speculate about the cause in customer conversations.
- **INC-2327:** the false quota warnings were retracted by email the same day. No customer was billed for the double count because Ledger reads monthly totals, and the hourly counts were corrected before month end.
- **INC-2278** is the reason Driftwood write lag now has its own alert.

## Counting

July–October 2026: one SEV-1, five SEV-2, four SEV-3. Every SEV-1 and SEV-2 needs a blameless postmortem within five business days of resolution.
