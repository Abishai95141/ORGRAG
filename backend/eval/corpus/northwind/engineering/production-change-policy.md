# Production change approval policy

**Owner:** VP Engineering · **Effective:** September 1, 2026 · **Applies to:** all changes to Northwind production systems

Code review rules live in the engineering handbook. This policy covers the extra approval that some production changes need on top of a reviewed pull request.

## Change types

**Standard changes** are pre-approved and need nothing beyond a normal PR review. Examples: scaling with `nwctl scale`, flipping a feature flag that is already rolled out in canary, rotating a credential with the automated rotation job, restarting a runner pod.

**Normal changes** need sign-off from the **change approver of the team that owns the affected service** (see the engineering team directory; owners are listed in the service catalog). Normal changes include:

- schema migrations on any production database;
- infrastructure configuration (infrastructure-as-code, deployment values, Kafka topic settings);
- data retention settings, lifecycle rules, or anything that deletes customer data on a schedule;
- changes to authentication, authorization or billing logic.

**Normal changes to a service that stores Restricted data** additionally need sign-off from the **Head of Security** or the security on-call delegate. Restricted services are marked in the service catalog.

**Emergency changes** may be approved by the incident commander during a declared incident. They must be reviewed retroactively by the owning team's change approver within two business days.

## How to get approval

1. Add the `change:normal` label to the PR and fill in the risk and rollback sections of the template.
2. Request review from the approver (or their delegate) as a CODEOWNERS reviewer.
3. For Restricted services, also request `@northwind/security`.
4. Approvals are valid for 7 days. If the change has not shipped by then, re-request.

## Retention changes

Any change that alters how long customer data is kept also requires an ADR, per the engineering handbook, and the ADR must be linked from the PR. Shortening retention needs a 30-day customer notice from the account team for Enterprise contracts that reference a retention period.

## Change freezes

- **Holiday freeze:** December 18, 2026 to January 4, 2027. Only standard and emergency changes.
- **Month-end billing freeze:** no normal changes to Ledger from the last business day of the month until invoices are sent (usually the 2nd).

Rollbacks remain allowed at any time, freeze or not.

## Exceptions

If the approver and delegate are both unavailable, the VP Engineering may approve. Exceptions are logged in `#eng-changes`.
