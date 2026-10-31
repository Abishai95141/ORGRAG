# Clinical and business systems inventory

**Maintained by:** HIPAA Security Officer (Ahmed Siddiqui) · **Reviewed:** quarterly · **Last review:** October 1, 2026

This inventory lists every Halcyon system approved to hold protected health information (PHI), plus key systems that must not. It is the "approved Halcyon systems" list referred to in the HIPAA privacy policy. Owning teams are shown by code; system stewards for each team are listed in the clinical systems change control policy.

## Systems that hold PHI

| System | What it's for | PHI held | Owning team | Vendor |
|---|---|---|---|---|
| Meridian EHR | Clinical record, orders, results, e-prescribing, secure messaging | Full clinical record | CI | Meridian Clinical Systems |
| ClearPath PM | Scheduling, registration, eligibility, claims | Demographics, insurance, appointments, balances | RCM | ClearPath Software |
| Switchyard | Interface engine routing HL7 messages between reference labs, the EHR and ClearPath | Lab orders and results in transit | INT | Built in-house |
| Pillbox | Medication reminder scheduler (SMS and app push) | Patient phone numbers, medication names and schedules | DHE-PE | Built in-house |
| Halcyon app | Patient app: digital intake, messaging, bill pay | Intake forms, insurance card images | DHE-PE | Built in-house |
| Larkspur | Current telehealth video visits | Visit metadata, in-call audio/video (not recorded) | VC | Larkspur Telehealth |
| Juniper | Telehealth platform v3 (in development; replaces Larkspur) | Visit metadata, waiting room queue | VC | Built in-house on the Coriander Media SDK |
| SafeReport | Patient safety event reporting | Event descriptions, patient identifiers | QPS | Built on the intranet |
| Quillon Ambient | AI scribe (pilot) | Visit audio, draft notes | CI | Quillon Ambient |

## Systems that must not hold PHI

| System | Notes |
|---|---|
| Slack | Never, including DMs |
| Email subject lines | Use `[secure]` encrypted email for PHI in the body |
| Halcyon Assist | Internal policy Q&A assistant. Policies only |
| Personal notes apps | Never |

## Team codes

CI = Clinical Informatics · RCM = Revenue Cycle Management · INT = Integration Services · DHE-PE = Digital Health Engineering, Patient Engagement squad · VC = Virtual Care · QPS = Quality & Patient Safety

## Adding a system

New systems that will hold PHI need a security risk assessment, a business associate agreement if a vendor is involved, and an entry here before go-live. Email the HIPAA Security Officer.
