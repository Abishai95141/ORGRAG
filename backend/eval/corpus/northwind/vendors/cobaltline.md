# Vendor register: Cobaltline

| Field | Value |
|---|---|
| Vendor | Cobaltline, Inc. |
| Service | Hosted log search and retention for application and infrastructure logs |
| Register ID | VEN-014 |
| Business owner | Observability team (OBS) |
| Status | Active |
| Data processed | Application logs from all production services. Logs are scrubbed at the agent: no event payloads, no credentials. May contain workspace IDs and IP addresses. |
| Highest data class | Confidential |
| Customer data? | Yes (workspace IDs, IP addresses) |
| Data Processing Agreement (DPA) | **Signed** — April 2, 2025 |
| Security review | Passed (`#vendor-review` ticket VR-88), re-reviewed July 2026 |
| SOC 2 Type II | Yes, report dated May 2026 |
| Data residency | US (us-east and us-west regions) |
| Contract term | 2 years |
| Renewal date | **March 31, 2027** |
| Annual cost | $96,000 |
| Notice period | 60 days before renewal |

## Notes

- Log retention inside Cobaltline is 30 days for application logs and 90 days for audit logs. Pipeline run logs are kept in Lookout, not Cobaltline.
- The renewal will be renegotiated in February 2027; OBS is comparing ingest-based pricing with our current flat-fee plan.
- Cobaltline is listed as a subprocessor on our public subprocessor page.
