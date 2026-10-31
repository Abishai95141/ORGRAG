# Emergency access to customer workspaces

**Owner:** Security team · **Applies to:** support engineers, solutions engineers and on-call engineers

Sometimes we need to look inside a customer's workspace to fix something urgently — a pipeline is dropping data, a connector is misconfigured, or the customer is locked out. This page explains how to get that access without breaking the rules in the information security policy.

## When it's allowed

Only when there is an open support case or a declared incident that needs it. "Curiosity" and "it would be faster" are not reasons. Every request must reference the case or incident ID.

## How to request it

1. Run `nwctl access request --workspace <id> --reason <case-or-incident-id>`.
2. A **second engineer** from the support or on-call rotation must approve the request in Teleport. You cannot approve your own request, and the approver must not be on the same case.
3. Access is read-only by default. Write access (for example, editing a pipeline on the customer's behalf) needs the customer's written consent in the case first.

## How long it lasts

Emergency workspace access expires after **2 hours**. This is deliberately shorter than the standard 8-hour Teleport production session. If you need more time, request again with an update on the case.

## What's recorded

- All sessions are recorded and kept for one year.
- The workspace's audit log shows "Northwind support accessed this workspace" with the time and case ID. Enterprise admins are emailed within 24 hours.
- Security reviews a sample of sessions every week, and every session that used write access.

## What you must never do

- Export or download event payloads. Restricted data never leaves production, so do not paste payloads into tickets, Slack, or a support reply. Use field names and counts instead.
- Share your session with someone else or screen-share it to a customer.
- Use emergency access to look at a workspace belonging to someone you know personally.

## If something goes wrong

If you see data you didn't expect to (for example another customer's data), stop, end the session and report it in `#security-help` immediately.
