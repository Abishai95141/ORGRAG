# Vendor register: Parcelwise

| Field | Value |
|---|---|
| Vendor | Parcelwise Ltd. |
| Service | Transactional email delivery (password resets, invites, billing emails, alert notifications) |
| Register ID | VEN-022 |
| Status | Active |
| Source system | Atlas sends recipient name, email address and template variables through the Parcelwise API |
| Data processed | Names and email addresses of customer users; email content for billing and alert notifications |
| Highest data class | Confidential |
| Customer data? | Yes |
| Data Processing Agreement (DPA) | **Signed** — November 20, 2023; amended September 2025 for EU standard contractual clauses |
| Security review | Passed (`#vendor-review` ticket VR-41) |
| SOC 2 Type II | Yes |
| Data residency | EU and US |
| Contract term | 1 year, auto-renews |
| Renewal date | **December 15, 2026** |
| Annual cost | $31,500 |
| Notice period | 30 days before renewal |

## Notes

- Parcelwise does **not** receive event payloads. Alert notification emails contain only pipeline names and error counts.
- Bounce and complaint webhooks go back to Atlas, which suppresses addresses that hard-bounce.
- Billing emails added a usage-breakdown link in October 2026; the link points at the Northwind app and contains no customer data itself.
- Engineering questions about what we send to Parcelwise should go to the team that owns the source system.
