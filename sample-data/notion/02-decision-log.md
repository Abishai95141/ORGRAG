# Engineering decision log

Every architectural decision gets an ADR, using the government ADR template. The full text of recent ones is in their own pages.

| ADR | Decision | Status | Date |
| --- | --- | --- | --- |
| ADR-001 | Build Book a Mooring in Python 3 with Django | Accepted | January 2025 |
| ADR-002 | Host on Heroku | Superseded by ADR-006 | February 2025 |
| ADR-003 | Take payments through GOV.UK Pay | Accepted | February 2025 |
| ADR-004 | Stop supporting Internet Explorer 11 | Accepted | March 2026 |
| ADR-005 | Send emails and texts through GOV.UK Notify | Accepted | March 2025 |
| ADR-006 | Move hosting to AWS ECS on Fargate | Accepted | March 2026 |
| ADR-007 | Run Postgres on Amazon RDS, with point-in-time recovery for 14 days | Accepted | March 2026 |
| ADR-008 | Use Keel (open source, self-hosted) for the Beacon pilot | Accepted | 14 October 2026 |
| ADR-009 | Write the permit pricing API in Python, not Go | Accepted | November 2026 |
| ADR-010 | Infrastructure changes need two approvals | Accepted, from 1 December 2026 | November 2026 |

> ADR-002 is kept for the record. We left Heroku because we needed private networking to the moorings register, which sits in our AWS account.
