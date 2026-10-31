# Vendor register: Saltmarsh Analytics

| Field | Value |
|---|---|
| Vendor | Saltmarsh Analytics GmbH |
| Service | Product analytics for the Northwind web app (funnels, feature adoption) |
| Register ID | VEN-027 |
| Business owner | Product (Mateo Alvarez) |
| Status | Active |
| Data processed | Pseudonymous click and page-view events from people using the Northwind app; hashed user IDs and workspace IDs |
| Highest data class | Confidential |
| Customer data? | Limited — usage of our app only. Customer event payloads: **No** |
| Data Processing Agreement (DPA) | **Signed** — June 12, 2025 |
| Security review | Passed (VR-64) |
| SOC 2 Type II | No; ISO 27001 certified |
| Data residency | EU (Frankfurt) |
| Contract term | 2 years |
| Renewal date | **June 30, 2027** |
| Annual cost | $27,000 |
| Notice period | 90 days before renewal |

## Notes

- The Saltmarsh snippet is loaded only after cookie consent in the EU and UK.
- Saltmarsh does not see anything customers send through Northwind Pipelines; it only sees how customers use our own UI. People sometimes confuse this with Beacon, our internal usage metering service, which counts customer events for billing.
- Retention inside Saltmarsh is 14 months.
- Mateo wants to evaluate replacing Saltmarsh with events routed through our own pipelines ("dogfooding") before the 2027 renewal.
