# Root cause analysis: lab results filed to the wrong patient chart (RCA-26-07)

**Event dates:** July 21–23, 2026 · **Classification:** Level 2 adverse event
**RCA lead:** Lorraine Pike, Quality & Patient Safety · **Completed:** August 28, 2026

## What happened

Over three days, **17 lab results** from Bayline Reference Laboratories were filed into the wrong patient's chart at the Riverside and Northgate clinics. A medical assistant at Riverside noticed on July 23 that a potassium result did not match the patient's history and raised it with the charge nurse. Two patients whose abnormal results had gone to another chart had follow-up delayed by four and six days; neither suffered lasting harm. All 17 results were moved to the correct charts by July 24 and both affected patients and the patients whose charts were wrongly updated were contacted.

## Timeline

- **July 20** — Bayline deployed a change to its results feed that moved the medical record number into a different HL7 segment, without notice.
- **July 21** — The interface engine could no longer find the MRN and fell back to its secondary matching rule.
- **July 23** — Mismatch spotted at Riverside; charge nurse filed SafeReport and stopped the Bayline feed.
- **July 24** — Feed restored with the MRN mapping corrected.

## Root causes

1. **Weak fallback matching.** When the MRN was missing, Switchyard matched results on last name and year of birth only. That rule had existed since 2021 and had never been exercised at volume.
2. **No monitoring of unmatched or fallback-matched messages.** Nobody was alerted that 100% of Bayline results were using the fallback rule.
3. **No change-notice obligation.** The Bayline agreement did not require advance notice of feed format changes.

## Action plan

| # | Action | Owner | Due | Status |
|---|---|---|---|---|
| 1 | Remove the name-and-birth-year fallback; unmatched results go to a manual work queue | Interface team | Sept 15 | Done |
| 2 | Alert when more than 1% of results from any lab use a fallback match in an hour | Interface team | Sept 30 | Done |
| 3 | Amend lab services agreement: 30 days' notice for feed changes | Lab contracts, Clinical Operations | Dec 31 | In progress |
| 4 | Add positive patient identification check before results are released to the patient portal | Clinical Informatics | Nov 30 | In progress |

## Lessons

Interfaces are clinical systems. A matching rule that is "almost never used" is still a patient-safety control. Changes to result routing are significant changes under clinical systems change control.

Reviewed by the Patient Safety Committee on September 10, 2026.
