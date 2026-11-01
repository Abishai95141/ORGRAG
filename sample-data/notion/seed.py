"""Create the sample Notion pages (*.md here) under a page you choose.

    python3 sample-data/notion/seed.py            # create
    python3 sample-data/notion/seed.py --dry-run  # convert only, and count blocks

Needs, in the environment or the repo's .env:
  NOTION_SEED_TOKEN   an internal integration's secret, with "Insert content"
  NOTION_PARENT_PAGE  the URL (or id) of a page shared with that integration
Pages that already exist under the parent (same title) are skipped.

The Markdown is the subset the pages use: headings, paragraphs, bulleted, numbered and
to-do lists, tables, quotes, `! ` callouts, **bold**, `code` and [links](url)."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
_INLINE = re.compile(r"\*\*(.+?)\*\*|`(.+?)`|\[(.+?)\]\((https?://[^)]+)\)")


def setting(name: str) -> str:
    if value := os.environ.get(name):
        return value
    env = HERE.parent.parent / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        key, _, value = line.partition("=")
        if key.strip() == name and value.strip():
            return value.strip().strip('"')
    sys.exit(f"Set {name} (in the environment or the repo's .env).")


def page_id(value: str) -> str:
    """The 32-hex id at the end of a Notion URL, or the id itself."""
    found = re.findall(r"[0-9a-f]{32}", value.replace("-", "").lower())
    if not found:
        sys.exit("NOTION_PARENT_PAGE isn't a Notion page URL or id.")
    return found[-1]


def rich(text: str) -> list[dict]:
    out, at = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > at:
            out.append({"type": "text", "text": {"content": text[at : m.start()]}})
        bold, code, label, url = m.groups()
        if bold:
            out.append({"type": "text", "text": {"content": bold}, "annotations": {"bold": True}})
        elif code:
            out.append({"type": "text", "text": {"content": code}, "annotations": {"code": True}})
        else:
            out.append({"type": "text", "text": {"content": label, "link": {"url": url}}})
        at = m.end()
    if at < len(text):
        out.append({"type": "text", "text": {"content": text[at:]}})
    return out


def block(kind: str, text: str, **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich(text), **extra}}


def convert(markdown: str) -> tuple[str, list[dict]]:
    """The page title (its `# ` heading) and its blocks."""
    title, blocks, table = "", [], []

    def flush_table():
        if not table:
            return
        rows = [r for r in table if not re.fullmatch(r"\|[\s:|-]+\|", r)]
        cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
        width = max(len(c) for c in cells)
        blocks.append(
            {
                "object": "block",
                "type": "table",
                "table": {
                    "table_width": width,
                    "has_column_header": True,
                    "has_row_header": False,
                    "children": [
                        {
                            "object": "block",
                            "type": "table_row",
                            "table_row": {
                                "cells": [rich(c) for c in row + [""] * (width - len(row))]
                            },
                        }
                        for row in cells
                    ],
                },
            }
        )
        table.clear()

    for line in markdown.splitlines():
        if line.startswith("|"):
            table.append(line.strip())
            continue
        flush_table()
        if not line.strip():
            continue
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("### "):
            blocks.append(block("heading_3", line[4:]))
        elif line.startswith("## "):
            blocks.append(block("heading_2", line[3:]))
        elif m := re.match(r"- \[([ x])\] (.*)", line):
            blocks.append(block("to_do", m.group(2), checked=m.group(1) == "x"))
        elif line.startswith("- "):
            blocks.append(block("bulleted_list_item", line[2:]))
        elif m := re.match(r"\d+\. (.*)", line):
            blocks.append(block("numbered_list_item", m.group(1)))
        elif line.startswith("> "):
            blocks.append(block("quote", line[2:]))
        elif line.startswith("! "):
            blocks.append(block("callout", line[2:], icon={"type": "emoji", "emoji": "📌"}))
        else:
            blocks.append(block("paragraph", line))
    flush_table()
    return title, blocks


def call(token: str, method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
    )
    for _ in range(5):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                time.sleep(0.35)  # Notion allows about three requests a second
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(int(exc.headers.get("Retry-After", "2")))
                continue
            detail = json.loads(exc.read() or b"{}").get("message", "")
            sys.exit(f"Notion {method} {path}: HTTP {exc.code} {detail}")
    sys.exit("Notion kept rate-limiting; try again in a minute.")


def main() -> int:
    pages = [convert(p.read_text()) for p in sorted(HERE.glob("*.md"))]
    if "--dry-run" in sys.argv:
        for title, blocks in pages:
            print(f"{title}: {len(blocks)} blocks")
        return 0
    token, parent = setting("NOTION_SEED_TOKEN"), page_id(setting("NOTION_PARENT_PAGE"))
    children = call(token, "GET", f"/blocks/{parent}/children?page_size=100")["results"]
    existing = {c["child_page"]["title"] for c in children if c["type"] == "child_page"}
    for title, blocks in pages:
        if title in existing:
            print(f"skip   {title}: already there")
            continue
        page = call(
            token,
            "POST",
            "/pages",
            {
                "parent": {"page_id": parent},
                "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
                "children": blocks[:100],
            },
        )
        for start in range(100, len(blocks), 100):
            call(
                token,
                "PATCH",
                f"/blocks/{page['id']}/children",
                {"children": blocks[start : start + 100]},
            )
        print(f"made   {title}")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
