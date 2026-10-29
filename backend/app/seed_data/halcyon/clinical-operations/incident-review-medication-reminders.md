# Incident review: duplicate medication reminders (IR-0412)

**Date:** September 3, 2026 · **Severity:** Moderate · **Patients affected:** 2,318

## What happened

Between 06:00 and 09:40 Eastern on September 3, patients enrolled in medication reminders received each reminder two to four times. Twelve patients replied that they had taken an extra dose of a once-daily medication. A clinical pharmacist contacted all twelve; none required treatment.

## Why it happened

A configuration change on September 2 moved the reminder scheduler to a new job runner. During the cutover, both the old and the new runners were active. Neither checked whether a reminder had already been sent, because the old system relied on being the only sender.

## Response

- 09:40 — Old runner disabled; duplicates stopped.
- 11:00 — Clinical pharmacy team began outreach to patients who reported extra doses.
- 14:00 — Apology message sent to all affected patients with guidance on what to do if they took an extra dose.

## Changes we made

1. Reminders now carry an idempotency key (patient, medication, scheduled time); a reminder with an existing key is never sent again.
2. Scheduler cutovers require a written runbook and a clinical safety sign-off.
3. Reminder messages now say "If you already took this dose, you don't need to take it again."

## Lessons

Patient-facing automation is a clinical system. Changes to it go through the same safety review as changes to clinical protocols.
