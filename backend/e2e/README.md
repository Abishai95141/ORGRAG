# End-to-end scenario

A realistic run of the whole system against a running API and worker: real Postgres,
real parsing (Docling, LibreOffice), real models. It isn't part of `pytest` (it costs
model calls and takes about ten minutes). Run it before a release or after a change that
crosses features.

**The setup.** Northwind Robotics, a warehouse-robot maker, and its partner Helix Labs.
- **Northwind's people:** owner Priya, admin Marcus, member Lena and viewer Tom, plus Jules, who is invited and then removed.
- **Helix:** owner Omar. Priya is also a Helix member.
- **Northwind's collections:**
  - an org-wide handbook (policies in DOCX, PDF and HTML; a CSV team directory);
  - org-wide Engineering (14 postmortems, a runbook, an architecture doc);
  - restricted Vendors & Contracts (an XLSX register, contract summaries, a vendor's email, a scanned amendment as a PNG);
  - restricted Leadership (compensation bands, a PPTX board update).

About 22 questions exercise the features the way people would:
- record filters, counts and group-bys;
- date arithmetic in deep mode;
- an email checked against a contract;
- a cross-org policy question;
- role look-ups (who holds "Head of Finance");
- an exhaustive scan (which incidents customers reported first);
- conflicting policies;
- OCR;
- an attachment checked against procurement rules;
- restricted data asked about by people who can't read it.

Around those questions the scenario covers:
- invites and roles;
- uploads in every format;
- record types (suggested, written for prose, activated, queried, resolved);
- the knowledge graph;
- verified answers;
- shared links;
- access requests;
- knowledge gaps and document requests, closed by uploading the missing document;
- agents;
- cross-org opt-out;
- API keys, and all eight MCP tools;
- conversation edits and cancelling an answer mid-stream;
- document boost, hide and delete;
- Insights, and the audit log (filters, pagination, CSV and JSONL exports, the cross-org entry in Helix's log).

Every REST endpoint is called; the run prints which weren't (only the Slack routes,
which `slack.py` covers).

```bash
cd backend
.venv/bin/python e2e/fixtures.py /tmp/strata-e2e/fixtures
# API (:8000) and worker running on a fresh database (alembic upgrade head), with an
# Anthropic key in .env:
.venv/bin/python e2e/scenario.py /tmp/strata-e2e/fixtures /tmp/strata-e2e/run
# Then stop the worker and run the Slack bot against the same database:
DATABASE_URL=postgresql+asyncpg://… .venv/bin/python -m e2e.slack /tmp/strata-e2e/run
```

**Slack.** `slack.py` runs the app in-process. Only Slack's Web API is simulated; the
database, agent and models are real, and the script runs the answer jobs itself. It
covers:
- OAuth install, including the wrong-user and forged-state errors;
- signed events, and acknowledging Slack's retries only once;
- the account-link flow;
- DM answers and in-channel answers (restricted collections never appear in a channel);
- a thread follow-up;
- refusing a channel shared with another organization;
- channel mappings, audit events, unlinking and disconnecting.

`followups.py RUN_DIR 3` replays the scenario's two multi-turn conversations and counts
follow-up answers that doubt or retract an earlier one unprompted (LOG D66).

**Output.**
- `results.json`: every check.
- `answers.json`: each question, its answer, trace and sources, for reading. Keyword checks catch missing facts, not bad prose.
- `users.json` and `state.json`: the run's accounts and ids. The password is random per run.

The checks assert the facts the fixtures hold. For example:
- the 8 vendors whose SOC 2 report expires before 2027-03-31;
- Amara Okafor approves a $12,000 sponsorship;
- Lena can't see salary bands until her access request is approved;
- Tom never gets a vendor-register fact.

The scenario's first runs found the bugs fixed in LOG D63–D69.
