# Clinical systems change control

**Owner:** Chief Medical Information Officer · **Effective:** October 2025, revised September 2026 after IR-0412

Halcyon treats patient-facing automation and clinical software as clinical systems. This policy says who approves changes to them.

## System stewards

Each owning team (codes as in the systems inventory) has one system steward who approves changes to that team's systems.

| Code | Team | System steward |
|---|---|---|
| CI | Clinical Informatics | Grace Oyelaran |
| RCM | Revenue Cycle Management | Tomás Herrera |
| INT | Integration Services | Owen Mbeki |
| DHE-PE | Digital Health Engineering — Patient Engagement | Felix Brandt |
| VC | Virtual Care | Dana Okafor |
| QPS | Quality & Patient Safety | Lorraine Pike |

## Change classes

**Minor changes** — cosmetic changes, report tweaks, adding a user. Steward approval only.

**Significant changes** — anything that affects:
- patient-facing automation (reminders, outreach messages, the patient app's clinical content);
- medication information, orders or results routing;
- clinical decision support rules or alerts;
- scheduler or job-runner cutovers.

Significant changes need **all three**: the system steward's approval, a **clinical safety sign-off from the CMIO, Dr. Anjali Rao** (or her delegate, Associate CMIO Dr. Benedict Osei), and review at the weekly Clinical Systems Change Advisory Board (CSCAB), Wednesdays at 14:00 Eastern.

**Emergency changes** — to stop ongoing patient harm. The on-call medical director may approve; CSCAB reviews at its next meeting.

## Runbooks

Cutovers (moving a job from one runner or server to another) require a written runbook that states how double-running is prevented. This rule came from IR-0412, where two reminder runners were active at the same time.

## Not to be confused with

The CMO, Dr. Kenji Watanabe, chairs the Patient Safety Committee and reviews sentinel events. He is not part of software change approval. Clinical *protocol* changes (for example, escalation thresholds) go through the Medical Executive Committee instead.

## Records

CSCAB decisions are logged in the change register on the intranet. Changes made without the required approvals are reported to QPS as safety events.
