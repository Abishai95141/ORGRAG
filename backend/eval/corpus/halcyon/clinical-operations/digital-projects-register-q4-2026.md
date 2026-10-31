# Halcyon digital projects register (Q4 2026)

**Maintained by:** Office of the CMIO (Dr. Anjali Rao) with the Digital Health project office · **Updated:** October 19, 2026

This register lists active clinical and business systems projects. Projects are known by codenames, and the codenames are what you will see in CSCAB minutes, Slack channel names and the change register. Team codes are the same as in the clinical and business systems inventory.

## Active projects

| Codename | Project | Owning team | Project lead | Status | Target |
|---|---|---|---|---|---|
| Wren | Meridian EHR upgrade to release 2027.1 (new order sets, results inbox redesign, updated e-prescribing module) | CI | Grace Oyelaran | Integrated testing | Q1 2027 |
| Heron | Positive patient identification check before lab results are released to the patient portal (RCA-26-07, action 4) | CI | Grace Oyelaran | Build complete; awaiting release | Q4 2026 |
| Kestrel | ClearPath PM scheduling rules that block patients located in states where the clinician is not licensed | RCM | Tomás Herrera | Build complete; awaiting release | Q4 2026 |
| Marigold | Automatic acknowledgement of refill requests sent through the Halcyon app, with routing into Pillbox | DHE-PE | Felix Brandt | Awaiting CSCAB | Q4 2026 |
| Sandpiper | Evaluation of the Quillon Ambient AI scribe pilot and the expansion decision | CI | Dr. Anjali Rao | Pilot running | Decision Q2 2027 |
| Plover | Replacement of the SafeReport intake form with structured fields and automatic level suggestions | QPS | Lorraine Pike | Discovery | Q3 2027 |
| Tern | RotaLeaf HR and leave system rollout | People team | Hannah Whitcombe | Live since October 1, 2026 | Closed |

Juniper is governed by its own steering committee and is not tracked in this register.

## How projects move through change control

1. The project lead brings each release to CSCAB as a change request (CHG number). Anything that touches orders, results routing, medication information or patient-facing automation is a significant change and needs the system steward, a clinical safety sign-off from the CMIO or her delegate, and CSCAB review.
2. Cutover and go-live dates are set by CSCAB, not in this register. The "Target" column is the planning quarter only.
3. When a project closes, the lead updates the systems inventory if a new system now holds PHI.

## Notes on specific projects

- **Wren:** Clinical Informatics is running integrated testing with Integration Services because Switchyard interfaces to all reference labs must be retested after the upgrade.
- **Heron:** this is the last open clinical informatics action from RCA-26-07. The Patient Safety Committee asked for a status update at its December meeting.
- **Marigold:** the acknowledgement message is patient-facing automation, so it follows the rules adopted after IR-0412, including the idempotency key on every message.
- **Sandpiper:** expansion beyond Riverside and Elm Park needs a CSCAB decision and a clinical safety review based on pilot data.
- **Tern:** closed. Leave requests now go through RotaLeaf.

## Adding a project

Email the Office of the CMIO with a one-page summary, the owning team and the system steward's agreement. Codenames are assigned by the project office from the bird list to avoid clashes.
