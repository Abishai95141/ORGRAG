"""Strata's Slack bot: people ask from Slack and get cited answers in the thread, with
the permissions they have in Strata.

- `client`   every Slack Web API call (one module, so tests swap the transport)
- `signing`  request signatures, and the signed OAuth state and account-link tokens
- `mrkdwn`   Markdown answers → Slack mrkdwn, split to Slack's message size
- `scope`    which orgs and collections a channel or DM may search
- `answer`   the `slack_answer` job: identity → scope → agent run → reply
"""
