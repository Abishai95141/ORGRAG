# Juniper steering committee notes — October 2026

**Date:** October 21, 2026 · **Chair:** Dana Okafor (Virtual Care)
**Present:** Dana Okafor, Rosa Delgado (Director of Clinical Operations), Dr. Anjali Rao (CMIO), Felix Brandt, Ahmed Siddiqui, Margaret Holloway (Privacy Officer), clinic managers from Riverside and Elm Park

## 1. Status

- Join success in internal testing: 98.7% across 1,900 test visits (goal is above 98%). Android join success is now level with iOS.
- Three-way calls with Polyglen interpreters work end to end. Caregiver links tested with 40 volunteer families.
- Screen sharing for lab results: done. Dr. Rao asked that shared results always show the patient's name and date of birth in the header, so a clinician cannot share the wrong patient's results without noticing.

## 2. Vendor decision (final)

The committee confirmed **Coriander Media** as the video SDK provider. The contract was completed in August. Self-hosting the media servers was rejected: too much operational load for Virtual Care's team of six.

## 3. Go-live plan

| Phase | Date | Scope |
|---|---|---|
| Pilot | **January 12, 2027** | Riverside, Elm Park and Northgate clinics |
| Network-wide | **March 2, 2027** | All clinics and the telehealth front desk |
| Larkspur shutdown | March 31, 2027 | After the month-to-month extension ends |

Go/no-go for the pilot is at the December 16 steering meeting. Criteria: join success above 98% for two consecutive weeks, zero open Level 1–2 safety findings, and CSCAB approval as a significant change.

## 4. Risks

- **Licensing checks.** Patients outside the clinician's licensed states must be blocked at scheduling. ClearPath's scheduling rules need an update from RCM; Tomás confirmed it is in their November release.
- **Training.** 410 clinicians need a 30-minute module before the network-wide date. Clinical Operations to schedule.
- **Holiday freeze.** No IT freeze at Halcyon, but clinic managers asked that pilot training not land in the week of December 21.

## 5. Decisions

- Recordings stay off. No exceptions, including for training purposes.
- Visit links will expire 2 hours after the scheduled end time, as in the requirements.
- Post-visit satisfaction survey will be sent by Tallgrass once their BAA is signed; until then, use the in-app survey.

## Next meeting

November 18, 2026.
