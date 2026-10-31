# Incident roles FAQ

Answers to the questions people ask most during their first incidents at Northwind. For severity definitions and alert-specific steps, see the on-call runbook.

**Who is in charge during an incident?**
The incident commander (IC). The IC is paged from a dedicated rotation when someone runs `/incident declare`, and for a SEV-1 must be assigned within 15 minutes. The IC coordinates; they do not debug. If you are the IC and find yourself reading logs, hand the role to someone else or ask for a deputy.

**What other roles are there?**
- **Communications lead** — writes status page updates and customer-facing messages. For a SEV-1 the first status page update goes out within 20 minutes, then at least every 30 minutes.
- **Scribe** — keeps the timeline in the incident channel. Usually the second person to join.
- **Responders** — the engineers from the owning team who actually investigate and fix.

**Who decides the severity?**
Whoever declares picks an initial severity. The IC can raise or lower it at any time. When unsure, declare higher; downgrading is cheap.

**Can the IC approve changes?**
Yes. During a declared incident the IC can approve emergency changes, including changes that would normally need the owning team's change approver. They are reviewed afterwards.

**When do we need a postmortem?**
Every SEV-1 and SEV-2 gets a blameless postmortem within five business days of resolution. The IC picks the author, usually the responder who knew the most about the failure. Postmortems are reviewed at the Thursday reliability meeting.

**Does a postmortem mean someone is in trouble?**
No. Postmortems are blameless: we look at the system and the process, not at individuals. Names appear only in the timeline.

**What about customers?**
For SEV-1 incidents affecting Enterprise workspaces, the account team checks whether SLA credits are owed under the 99.95% commitment and contacts the customer within one business day.

**I'm new. Can I be IC?**
Not until you have shadowed at least two incidents and completed the IC training. Ask in `#incident-practice`.
