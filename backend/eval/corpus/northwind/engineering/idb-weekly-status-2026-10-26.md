# Identity & Billing weekly status: week of October 26, 2026

**Author:** HV · **Audience:** `#team-idb`, `#eng-leads` · **Posted:** Monday, October 26, 2026

Short version: Undertow has a date, Lantern beta sign-ups are ahead of plan, and the INC-2340 follow-ups are nearly closed. Watch out for the month-end billing freeze this Friday.

## Undertow

- Dual-running has been on since October 19: 5% of Growth workspaces now have their card payments collected through the new provider, with Ledger writing to both. No mismatches so far in reconciliation.
- **Full cutover is scheduled for January 19, 2027**, after the holiday freeze and after January invoices have gone out. AM owns the cutover plan.
- Customer token migration rehearsal #2 was planned for October 30, but that's the last business day of the month, so it falls in the month-end billing freeze. **It moves to November 12.** AM runs it; NH will sit in to check what customers would see on the billing emails.
- Open risk: the new provider's webhook retries are faster than the old one's. AM is making sure our handler is idempotent before cutover (same class of bug as INC-2340).

## Lantern

- Beta waitlist: **23 workspaces** signed up so far, against a target of 40 by the February beta. NH is running the sign-up campaign with the account team.
- The invoice history view now hides voided duplicate invoices, the follow-up NH raised after INC-2340.
- The usage view reads hourly counts from Beacon through the API RS's team delivered on October 20, so the portal doesn't have to query Ledger.
- Plan changes in the portal are blocked on Undertow: we're not building plan changes against the old provider's API only to throw them away.

## INC-2340 follow-ups

| Item | Owner | Status |
|---|---|---|
| Idempotency key per workspace and billing period on invoice creation | AM | Done (shipped October 8) |
| Daily reconciliation report: invoices vs charges | AM | Done |
| Alert when the provider webhook times out during a billing batch | AM | Open, due November 6 |
| Hide voided duplicates in the Lantern invoice history | NH | Done |

## Keystone

The SAML metadata refresh now validates the new metadata before swapping it in (INC-2302 follow-up). Shipped October 15; no repeat since.

## Staffing and approvals

- Harriet and Arjun are **both out November 24–27** (Thanksgiving week). Plan any normal changes to Ledger or Keystone around it, or follow the exceptions route in the production change approval policy.
- We're interviewing for a second billing engineer; two onsites this week.

## Reminders

- Month-end billing freeze: Friday October 30 until invoices are sent (expected November 2). No normal changes to Ledger in that window.
- Next status: November 2.
