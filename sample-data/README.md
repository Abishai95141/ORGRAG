# Sample data for every connector

A realistic set of sources for trying Strata end to end: a website, a GitHub repository,
files to upload, files for Google Drive, and conversations and pages to put into Slack
and Notion. [questions.md](questions.md) has 23 questions with known answers over all of
it, including ones that need several sources, ones where a fact changed, and one only
some people may see.

**The story.** Harbourline Digital is a fictional public-sector digital team. It builds
Book a Mooring (a permit service in private beta) and is piloting Beacon, an internal AI
assistant. It follows the UK Government Digital Service's public standards, so its
library is real: the GOV.UK Service Manual, the GDS Way, GOV.UK's RFCs, and GDS
publications. Its Slack and Notion content (written for this set) refers to them.

| Source | What | Size |
|---|---|---|
| Website | [GOV.UK Service Manual](https://www.gov.uk/service-manual) | 227 pages, about 3 minutes to crawl |
| GitHub | [alphagov/gds-way](https://github.com/alphagov/gds-way) and [alphagov/govuk-rfcs](https://github.com/alphagov/govuk-rfcs) | 67 and 102 documents |
| Upload | 11 GDS publications: reports and playbooks (PDF), an ADR template (Word), a transparency template (Excel), 7,932 .gov.uk domains (CSV) | 12 MB |
| Google Drive | 18 files in 4 folders: working group notes, steering board minutes, data maturity workbooks, procurement | 9 MB |
| Slack | 7 channels, 62 messages with threads; `#leads` is private | `slack/*.txt` |
| Notion | 12 pages: directory, decision log, an ADR, a postmortem, assessment prep, project brief, meeting notes, OKRs | `notion/*.md` |

## 1. Download the files

```bash
python3 sample-data/fetch.py
```

Files land in `sample-data/upload/` and `sample-data/drive/` (not committed).

## 2. Website

Add source → Website: URL `https://www.gov.uk/service-manual`, include paths
`/service-manual`, max pages 300, max depth 3.

## 3. GitHub

Add source → GitHub, once for each: `alphagov/gds-way` and `alphagov/govuk-rfcs`. No
token is needed for public repositories.

## 4. Upload

Add source → Upload, then drop in everything in `sample-data/upload/`.

## 5. Google Drive

1. Set up the Drive source's service account (Add source → Google Drive shows the steps).
2. In Google Drive, upload the four folders in `sample-data/drive/` (New → Folder upload).
3. Share each folder with the service account's email as a Viewer. Sharing them one by
   one lets you test picking: Check access lists all four.
4. Pick some or all of them, and add the source.

## 6. Slack

Strata reads Slack with its own app (Add source → Slack shows the steps). Posting the
sample conversations needs a second app with write scopes, which you can delete
afterwards:

1. At [api.slack.com/apps](https://api.slack.com/apps): Create New App → From a
   manifest → your workspace → paste `slack/seed-app-manifest.json` → Create.
2. Install it to the workspace and copy its Bot User OAuth Token.
3. Put it in the repo's `.env` as `SLACK_SEED_TOKEN=xoxb-…`, then:

```bash
python3 sample-data/slack/seed.py
```

It creates the channels and posts each message under the person's name. Running it again
skips channels that are already seeded. Then type `/invite @Strata` in `#leads` so
Strata's app can read it.

To test permissions, use two Slack sources: the public channels into a collection the
whole org sees, and `#leads` into a restricted collection (question 22).

## 7. Notion

1. At [notion.so/my-integrations](https://www.notion.so/my-integrations), create an
   internal integration with "Read content" and "Insert content".
2. Create an empty page (say "Harbourline handbook"), then share it with the integration:
   ••• → Connections → add it.
3. Put both in the repo's `.env`, then:

```bash
NOTION_SEED_TOKEN=secret_…
NOTION_PARENT_PAGE=https://www.notion.so/Harbourline-handbook-…
```

```bash
python3 sample-data/notion/seed.py
```

The same integration's token works for Strata's Notion source.

## Licences

- GOV.UK publications and the Service Manual: Crown copyright, [Open Government Licence
  v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
  `files.json` lists every file's source.
- The GDS Way: MIT. GOV.UK RFCs: published by GDS on GitHub.
- Harbourline Digital, its people and its Slack and Notion content are fictional.
