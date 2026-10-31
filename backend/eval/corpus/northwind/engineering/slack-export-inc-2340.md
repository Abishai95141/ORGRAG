# Slack export: #inc-2340-ledger-double-invoices

*Exported October 9, 2026 for the postmortem. Times are UTC. Some messages trimmed.*

**Tue Oct 6**

**09:12 Arjun Mehta:** seeing a lot of "you charged us twice" tickets coming in from support. checking ledger
**09:15 Arjun Mehta:** yeah. month-end invoicing job ran twice for some workspaces. invoices for September exist twice with different invoice numbers
**09:16 Arjun Mehta:** `/incident declare sev2 "Duplicate September invoices from Ledger"`
**09:16 incident-bot:** Incident INC-2340 declared (SEV-2). IC paged: Kofi Mensah.
**09:21 Kofi Mensah:** IC here. Arjun, what's the blast radius and is it still happening?
**09:24 Arjun Mehta:** job finished at 06:40. not ongoing. counting now
**09:30 Harriet Vance:** I'm here. HV for IDB. Do we know if the payment provider actually collected twice or just invoiced twice?
**09:38 Arjun Mehta:** both for card customers on auto-pay. 312 duplicate invoices across 287 workspaces. 201 of the invoices were auto-charged
**09:39 Kofi Mensah:** ok. comms lead please draft an email to affected billing contacts. Harriet can you own the refund decision
**09:41 Harriet Vance:** refund all 201 charges today, void the other 111 invoices. no need to wait for customers to ask
**09:55 Noor Haddad:** draft email in the doc, pls review
**10:20 Arjun Mehta:** root cause: the payment provider's webhook for the first batch timed out at 06:05, our retry logic re-ran the batch, and invoice creation isn't idempotent. classic
**10:22 Kofi Mensah:** fix plan?
**10:31 Arjun Mehta:** idempotency key per (workspace, billing period) on invoice creation. PR up in ~1h. it's a billing logic change so it needs HV as change approver
**10:33 Harriet Vance:** I'll review. Kofi, approving as IC is fine too since we're in a declared incident
**11:47 Arjun Mehta:** PR merged, deployed. reran a dry run of the job, zero duplicates
**12:30 Harriet Vance:** refunds issued for all 201 charges, $184,300 total. voids done
**12:35 Kofi Mensah:** resolving INC-2340. postmortem owner Arjun, due Oct 13 (five business days). thanks all

**Wed Oct 7**

**08:02 Noor Haddad:** fyi this pushes Lantern's invoice history view a bit, we need to hide voided duplicates in the portal
**08:10 Harriet Vance:** add it to the Lantern board, not this channel 🙏
