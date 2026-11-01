"""Post the sample Slack conversations (*.txt here) into a workspace.

    python3 sample-data/slack/seed.py            # post
    python3 sample-data/slack/seed.py --dry-run  # show what would be posted

Needs SLACK_SEED_TOKEN (a bot token, xoxb-…) in the environment or in the repo's .env,
from an app made with seed-app-manifest.json: it creates the channels, then posts each
message under the person's name (chat:write.customize). A channel that already has its
first message is skipped, so running it again doesn't duplicate anything.

File format: `# purpose: …` and `# private: true` headers, then `Name: message` lines;
an indented line is a reply in the thread of the message above it."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PEOPLE = json.loads((HERE / "people.json").read_text())
ORDER = [
    "general",
    "eng-standards",
    "book-a-mooring",
    "incidents",
    "ai-beacon",
    "data-team",
    "leads",
]


def token() -> str:
    if value := os.environ.get("SLACK_SEED_TOKEN"):
        return value
    env = HERE.parent.parent / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        key, _, value = line.partition("=")
        if key.strip() == "SLACK_SEED_TOKEN" and value.strip():
            return value.strip().strip('"')
    sys.exit("Set SLACK_SEED_TOKEN (in the environment or the repo's .env).")


def parse(path: Path) -> dict:
    channel = {"name": path.stem, "purpose": "", "private": False, "messages": []}
    for raw in path.read_text().splitlines():
        if not raw.strip():
            continue
        if raw.startswith("# purpose:"):
            channel["purpose"] = raw.split(":", 1)[1].strip()
        elif raw.startswith("# private:"):
            channel["private"] = raw.split(":", 1)[1].strip() == "true"
        else:
            who, _, text = raw.strip().partition(": ")
            if who not in PEOPLE:
                sys.exit(f"{path.name}: unknown person {who!r}")
            message = {"who": who, "text": text, "replies": []}
            if raw.startswith(" "):
                channel["messages"][-1]["replies"].append(message)
            else:
                channel["messages"].append(message)
    return channel


class Slack:
    def __init__(self, bot_token: str):
        self.token = bot_token

    def call(self, method: str, **params):
        body = json.dumps(params).encode()
        request = urllib.request.Request(
            f"https://slack.com/api/{method}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        if method in ("conversations.list", "conversations.history"):  # form-encoded reads
            request = urllib.request.Request(
                f"https://slack.com/api/{method}?{urllib.parse.urlencode(params)}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
        for _ in range(5):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    time.sleep(int(exc.headers.get("Retry-After", "5")))
                    continue
                raise
            if data.get("ok"):
                return data
            if data.get("error") == "ratelimited":
                time.sleep(5)
                continue
            raise RuntimeError(f"{method}: {data.get('error')} {data.get('needed', '')}".strip())
        raise RuntimeError(f"{method}: still rate-limited")

    def channels(self) -> dict[str, dict]:
        found, cursor = {}, ""
        while True:
            page = self.call(
                "conversations.list",
                types="public_channel,private_channel",
                exclude_archived="true",
                limit=200,
                cursor=cursor,
            )
            found.update({c["name"]: c for c in page["channels"]})
            cursor = page.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                return found

    def post(self, channel_id: str, message: dict, thread_ts: str | None = None) -> str:
        person = PEOPLE[message["who"]]
        extra = {"thread_ts": thread_ts} if thread_ts else {}
        sent = self.call(
            "chat.postMessage",
            channel=channel_id,
            text=message["text"],
            username=message["who"],
            icon_emoji=person["icon"],
            unfurl_links=False,
            unfurl_media=False,
            **extra,
        )
        time.sleep(1.1)  # chat.postMessage allows about one message a second per channel
        return sent["ts"]


def main() -> int:
    dry = "--dry-run" in sys.argv
    channels = [parse(HERE / f"{name}.txt") for name in ORDER]
    if dry:
        for c in channels:
            n = sum(1 + len(m["replies"]) for m in c["messages"])
            print(f"#{c['name']}{' (private)' if c['private'] else ''}: {n} messages")
        return 0
    slack = Slack(token())
    me = slack.call("auth.test")
    print(f"Posting as the app in {me['team']}")
    existing = slack.channels()
    for c in channels:
        found = existing.get(c["name"])
        if found is None:
            found = slack.call("conversations.create", name=c["name"], is_private=c["private"])[
                "channel"
            ]
            print(f"created #{c['name']}")
        elif not found.get("is_member"):
            if c["private"]:
                print(f"skip #{c['name']}: a private channel the app isn't in")
                continue
            slack.call("conversations.join", channel=found["id"])
        history = slack.call("conversations.history", channel=found["id"], limit=200)["messages"]
        if any(m.get("text") == c["messages"][0]["text"] for m in history):
            print(f"skip #{c['name']}: already seeded")
            continue
        if c["purpose"]:
            slack.call("conversations.setPurpose", channel=found["id"], purpose=c["purpose"])
        count = 0
        for message in c["messages"]:
            ts = slack.post(found["id"], message)
            count += 1
            for reply in message["replies"]:
                slack.post(found["id"], reply, thread_ts=ts)
                count += 1
        print(f"posted {count} messages in #{c['name']}")
    print("done. Private channels: type /invite @Strata in them so Strata's own app can read them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
