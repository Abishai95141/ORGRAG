# Test questions

Questions with known answers over the sample sources, for trying Strata (or comparing
it with another tool on the same data). "Sources" names where the answer lives; the
traps say what a weak answer gets wrong.

## One source each

| # | Question | Answer | Sources |
|---|---|---|---|
| 1 | How many points does the Service Standard have, and what is point 5? | 14; "Make sure everyone can use the service" | Website |
| 2 | Which backend languages does the GDS Way say new development should use, and when is Go appropriate? | Java, Python, Ruby, JavaScript/Node.js. Go is for platform engineering (Kubernetes operators and controllers, CLIs, Crossplane functions), owned by the GOV.UK Platform Engineering team | GitHub: gds-way |
| 3 | Which language does the GDS Way say not to use for new projects, and why? | Scala: GOV.UK Licensing is the last Scala application and it's hard to support for lack of skills | GitHub: gds-way |
| 4 | What does GOV.UK RFC-178 require before external dependency updates can be auto-merged, and why that number of days? | A Dependabot `cooldown` with `default-days` of at least 3; most malicious packages are pulled within hours, so 3 days is conservative | GitHub: govuk-rfcs |
| 5 | In the cross-government M365 Copilot experiment, how much time did people save, and how many wouldn't go back? | 26 minutes a day on average; 82% wouldn't want to return to working without it (satisfaction 7.7/10, recommendation 8.2/10) | Upload |
| 6 | List the AI Playbook's 10 principles. | Know what AI is and its limits; use it lawfully, ethically and responsibly; securely; meaningful human control at the right stage; manage the AI life cycle; right tool for the job; open and collaborative; work with commercial colleagues from the start; have the skills and expertise; use the principles alongside your organisation's policies with the right assurance | Upload |
| 7 | How many domain names are on the .gov.uk list as of 31 March 2026? | 7,932 | Upload (CSV). Trap: counting from a few retrieved chunks |
| 8 | Who chaired the Data Standards Authority Steering Board on 6 May 2025, and what were the two agenda items? | Jenny Brooker (GDS); data management (DQAP guidance) and Essential Shared Data Assets with the data ownership model | Drive |
| 9 | When did the Vulnerabilities Working Group hold its 6th meeting? | Wednesday 24 September 2025, 10:00 to 11:00, on Teams | Drive |
| 10 | Who is on call in week 2 of December? | Priya Raman (she swapped with Sam Doyle, who now has week 3) | Slack. Trap: the original rota says Sam |
| 11 | How much does on-call pay? | £150 a week, plus £40 for each call-out outside working hours | Notion |
| 12 | Which ADR was superseded, by which, and why? | ADR-002 (Heroku) by ADR-006 (AWS ECS on Fargate), for private networking to the moorings register in AWS | Notion |

## Across sources, and things that changed

| # | Question | Answer | Sources |
|---|---|---|---|
| 13 | When is the Book a Mooring beta assessment? | Tuesday 16 December 2026, 10:00 to 14:00 (moved from 9 December: the panel chair couldn't make it) | Notion, Slack #harbourline-leads. Trap: #harbourline-book-a-mooring still says 9 December |
| 14 | What was Book a Mooring's completion rate in October? | 89%; the first figure, 91%, double counted resumed applications | Slack. Trap: 91% |
| 15 | What caused INC-2026-014, how many applicants were affected, and where do its actions stand? | v2.31.0 raised web workers from 4 to 12 without raising the connection pool. 143 applicants saw an error, 61 completed that day. Pool sizing: done (v2.32.0). Pool alert: live (26 November leads sync). Load test: not started, due 12 December | Slack, Notion. Trap: the postmortem page still says the alert is in progress |
| 16 | Does ADR-009 follow the GDS Way's language guidance? | Yes: Python is a core backend language, and the GDS Way keeps Go for platform engineering; the pricing API is application code | Notion, GitHub, Slack |
| 17 | How does Beacon's time-saving target compare with the Copilot experiment? | Beacon aims for 20 minutes a day; the experiment reported 26 | Notion or Slack, Upload |
| 18 | Which AI Playbook principle will the panel push hardest on for Beacon, and how does Beacon meet it? | Principle 4, meaningful human control at the right stage: Beacon only drafts, and a person decides | Slack, Notion, Upload |
| 19 | Which Service Standard points is Book a Mooring weakest on, and why? | Point 5 is red: 3 WCAG 2.2 AA failures from the Shoreline Access audit (date picker focus, error summary links, vessel type label). Amber: 2, 4, 9 and 14 | Notion, Slack, Website |
| 20 | How does our Dependabot policy compare with GOV.UK's RFC-178? | Ours: merge Dependabot pull requests within 5 working days, critical CVEs within 48 hours. RFC-178: external updates may auto-merge only after a cooldown of at least 3 days | Slack, GitHub |
| 21 | What did the DSA Steering Board discuss about data quality action plans, and what is our own DQAP for? | The board sought endorsement for guidance and artefacts supporting DQAPs (6 May 2025). Ours covers the moorings register: de-duplicate 3,412 vessel records, validate berth lengths, a monthly completeness report, all due 30 January 2027 | Drive, Slack |

## Permissions and gaps

| # | Question | Answer | Sources |
|---|---|---|---|
| 22 | What is the 2027/28 budget? | £2.4m, down from £2.7m, only for people who can see #harbourline-leads. Everyone else should get no answer, not a guess | Slack #harbourline-leads (private) |
| 23 | What is Harbourline's parental leave policy? | Not in any source: the answer should say so | None |
