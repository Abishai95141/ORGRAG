# Northwind Engineering Handbook

This handbook describes how the Northwind Labs engineering team plans, builds, reviews and ships software. It applies to everyone who commits to the `northwind/*` repositories, including contractors.

## Planning

Work is planned in six-week cycles followed by a two-week cool-down. Each cycle starts with a pitch review on the first Monday, where product and engineering leads agree on at most three "big bets" and a handful of small batch items. Anything not picked is dropped, not carried over; if it still matters, it gets re-pitched.

During cool-down, engineers fix bugs, pay down tech debt, and explore. Cool-down is protected time: no new customer commitments may be scheduled into it.

## Code review

- Every change to `main` goes through a pull request with at least one approving review.
- Changes touching billing, authentication, or the data plane (`ingest/`, `runner/`) need two approvals, one of them from a code owner listed in `CODEOWNERS`.
- Reviewers should respond within one business day. If a PR has been waiting more than 24 hours, the author may ping `#eng-review`.
- Keep PRs under roughly 400 changed lines. Larger changes should be split behind a feature flag.

## Testing

Unit test coverage on new code is expected to stay above 80%. Integration tests run against a disposable Postgres and a local Kafka in CI. Flaky tests are quarantined within 24 hours by the person who notices them, and an issue is filed with the `flaky-test` label.

## Deploys

We deploy to production continuously from `main`, Monday through Thursday, 09:00–16:00 Pacific. Friday deploys require approval from the on-call engineering manager. Every deploy goes to the canary cell first (5% of traffic) and is automatically promoted after 20 minutes if error rates and p95 latency stay within 10% of the baseline.

Rollbacks are always allowed, at any time, without approval.

## Feature flags

Flags live in LaunchDarkly. A flag older than 90 days that is fully rolled out must be removed; the weekly flag report in `#eng-flags` lists offenders. Kill switches for the data plane are permanent and exempt.

## Architecture decisions

Significant decisions are recorded as ADRs in `docs/adr/`. An ADR is required for: adding a new datastore, adding a new language to the stack, changing a public API contract, or any change that alters data retention.
