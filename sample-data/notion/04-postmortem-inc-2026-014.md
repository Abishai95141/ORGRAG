# Postmortem: INC-2026-014, Book a Mooring 502 errors

Date: Tuesday 14 October 2026. Severity: 2. Incident lead: Marcus Feld. Comms: Tom Okafor.

## Summary

Release v2.31.0 raised the web workers in each container from 4 to 12 without raising the database connection pool. The pool was exhausted and about 22% of requests failed with 502 errors from 14:05 until the rollback took effect at 14:41.

## Impact

- 143 applicants saw an error. 61 of them completed their application the same day.
- No data was lost and no payments were taken twice.
- The status banner was up from 14:18 to 15:35.

## Timeline

- 14:05 The error rate alert fired.
- 14:12 Marcus took incident lead.
- 14:20 Connection pool exhaustion suspected.
- 14:34 Rolled back to v2.30.4.
- 14:41 Error rate under 0.1%.
- 15:30 Incident resolved.

## What we're changing

| Action | Owner | Due | Status |
| --- | --- | --- | --- |
| Size the connection pool from the worker count | Sam Doyle | 31 October 2026 | Done in v2.32.0 |
| Alert when the pool is more than 80% used | Marcus Feld | 7 November 2026 | In progress |
| Add a load test to the release pipeline | Priya Raman | 12 December 2026 | Not started |

## What went well

The alert fired within a minute, and rolling back took under 15 minutes from the decision.
