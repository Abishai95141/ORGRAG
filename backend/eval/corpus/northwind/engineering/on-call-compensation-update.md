# On-call compensation update (October 2026)

**From:** Dmitri Sokolov (VP Engineering) and Renata Kowal (CFO)
**Date:** October 19, 2026
**Effective:** on-call shifts starting on or after Tuesday, November 3, 2026

## Summary

This memo replaces the compensation bullet in the on-call runbook. Everything else in the runbook (rotation length, severity levels, escalation) is unchanged.

| | Before (runbook) | From November 3, 2026 |
|---|---|---|
| Primary on-call stipend | $400 per week | **$500 per week** |
| Secondary on-call stipend | none | **$200 per week** |
| Page outside business hours | $100 per page | **$150 per page** |
| Cap on out-of-hours page pay | none | $1,500 per week |

## Why we're changing it

Page volume for the Data Plane rotation rose about 40% after the August ingest outage while we tightened alert thresholds, and the secondary role has been doing real work without any recognition. The engagement survey also flagged on-call pay as below market for our stage.

## How it's paid

Stipends and page pay are added to the next payroll after the shift ends. PagerDuty is the record: if you swapped a shift, make sure the swap is reflected in PagerDuty, otherwise the pay goes to whoever PagerDuty shows. Pages that are acknowledged and resolved as duplicates of an earlier page in the same incident count once.

## Time off in lieu

If you are paged for more than three hours between 22:00 and 07:00 local time, take the next morning off. This doesn't need approval; just tell your manager and post in your team channel.

## Questions

Ask in `#eng-leads` or message Dmitri directly. The on-call runbook will be updated to link to this memo.
