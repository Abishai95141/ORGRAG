"""The GitHub connector reads a repository from one archive download: no REST call for a
public repository (the per-IP limit of 60 an hour made syncs fail), one with a token."""

import io
import tarfile
import time

import httpx
import pytest

from app.connectors import github
from app.connectors.base import ConnectorError
from app.connectors.github import GitHubConnector

SHA = "38273c7f0edbab29f2563dfe45c396b0d2cbaeab"


def _tarball(files: dict[str, bytes], commit: str | None = SHA) -> bytes:
    buf = io.BytesIO()
    pax = {"comment": commit} if commit else {}
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers=pax) as tar:
        root = tarfile.TarInfo("acme-handbook-38273c7")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        for path, data in files.items():
            info = tarfile.TarInfo(f"acme-handbook-38273c7/{path}")
            info.size, info.mtime = len(data), 1_760_000_000
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


FILES = {
    "README.md": b"# Handbook\n\nWelcome.",
    "docs/leave.md": b"# Leave\n\nSixteen weeks.",
    "docs/big.md": b"x" * github.MAX_FILE_BYTES,  # too large to index
    "src/app.py": b"print('code is not indexed')",
    "logo.png": b"\x89PNG",
}


def _github(monkeypatch, routes):
    calls = []

    def handler(request: httpx.Request):
        calls.append((str(request.url), request.headers.get("authorization")))
        return routes(request)

    monkeypatch.setattr(github, "_transport", httpx.MockTransport(handler))
    return calls


async def _docs(config, secrets=None):
    async def progress(*_):
        pass

    connector = GitHubConnector(config, secrets or {})
    return connector, [d async for d in connector.iter_documents(progress)]


async def test_a_public_repository_needs_no_api_request(monkeypatch):
    codeload = f"https://codeload.github.com/acme/handbook/tar.gz/{SHA}"

    def routes(request):
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": codeload})
        if str(request.url) == codeload:
            return httpx.Response(200, content=_tarball(FILES))
        return httpx.Response(500)

    calls = _github(monkeypatch, routes)
    connector, docs = await _docs({"repo": "https://github.com/acme/handbook.git"})
    assert [d.external_id for d in docs] == ["README.md", "docs/leave.md"]
    assert not any("api.github.com" in url for url, _ in calls)
    assert calls[0][0] == "https://github.com/acme/handbook/archive/HEAD.tar.gz"
    leave = docs[1]
    assert leave.data == b"# Leave\n\nSixteen weeks." and leave.mime == "text/markdown"
    # Links pin the commit that was indexed; the archive's dates are the commit's.
    assert leave.url == f"https://github.com/acme/handbook/blob/{SHA}/docs/leave.md"
    assert leave.metadata["commit"] == SHA and leave.updated_at.year == 2025
    assert connector.complete


async def test_a_private_repository_uses_the_token_for_one_api_request(monkeypatch):
    def routes(request):
        if request.url.host == "api.github.com":
            assert request.headers["authorization"] == "Bearer ghp_test"
            return httpx.Response(
                302, headers={"location": "https://codeload.github.com/x?token=t"}
            )
        if request.url.host == "codeload.github.com":
            assert "authorization" not in request.headers  # not sent to another host
            return httpx.Response(200, content=_tarball(FILES, commit=None))
        return httpx.Response(500)

    calls = _github(monkeypatch, routes)
    _, docs = await _docs({"repo": "acme/handbook", "branch": "main", "paths": ["docs"]},
                          {"token": "ghp_test"})  # fmt: skip
    assert calls[0][0] == "https://api.github.com/repos/acme/handbook/tarball/main"
    assert [d.external_id for d in docs] == ["docs/leave.md"]  # paths filter
    assert docs[0].url == "https://github.com/acme/handbook/blob/main/docs/leave.md"


@pytest.mark.parametrize(
    ("status", "token", "message"),
    [
        (404, None, "add an access token"),
        (404, "ghp_x", "token can read the repository"),
        (401, "ghp_x", "rejected the access token"),
        (403, "ghp_x", "rate-limiting this token until"),
    ],
)
async def test_failures_say_what_to_do(monkeypatch, status, token, message):
    reset = {"x-ratelimit-reset": str(int(time.time()) + 600)}
    _github(monkeypatch, lambda request: httpx.Response(status, headers=reset))
    with pytest.raises(ConnectorError, match=message):
        await _docs({"repo": "acme/handbook"}, {"token": token} if token else None)


async def test_markdown_templates_are_read_but_other_templates_are_not(monkeypatch):
    files = {
        "source/manuals/code-review.html.md.erb": b"# Code review\n\nTwo approvals.",
        "app/views/index.html.erb": b"<%= render 'nav' %>",
    }
    _github(monkeypatch, lambda request: httpx.Response(200, content=_tarball(files)))
    _, docs = await _docs({"repo": "alphagov/gds-way"})
    assert [(d.external_id, d.mime) for d in docs] == [
        ("source/manuals/code-review.html.md.erb", "text/markdown")
    ]


async def test_a_template_page_is_indexed_as_the_page_it_renders(monkeypatch):
    """Middleman pages keep their title in front matter and render it with ERB: indexed
    as written, every page was titled `<%= current_page.data.title %>`."""
    page = b"""---
title: "How to review code"
last_reviewed_on: 2025-06-17
---

<% content_for :sidebar do %>
<ul><li>Sidebar</li></ul>
<% end %>

<%= partial 'partials/site-banner' %>

# <%= current_page.data.title %>

Reviews keep code maintainable.
"""
    untitled = b"<%= partial 'banner' %>\n\nNo heading here.\n"
    files = {"source/review.html.md.erb": page, "source/plain.md.erb": untitled}
    _github(monkeypatch, lambda request: httpx.Response(200, content=_tarball(files)))
    _, docs = await _docs({"repo": "alphagov/gds-way"})
    by_path = {d.external_id: d.data.decode() for d in docs}
    review = by_path["source/review.html.md.erb"]
    assert review.startswith("# How to review code")
    assert "Reviews keep code maintainable." in review
    assert "<%" not in review and "Sidebar" not in review and "last_reviewed_on" not in review
    assert by_path["source/plain.md.erb"].strip() == "No heading here."


async def test_ordinary_markdown_is_left_exactly_as_written(monkeypatch):
    text = b"---\ntitle: Kept\n---\n\n# Page\n\nShows `<%= tag %>` in a code sample.\n"
    _github(monkeypatch, lambda request: httpx.Response(200, content=_tarball({"a.md": text})))
    _, docs = await _docs({"repo": "acme/handbook"})
    assert docs[0].data == text


async def test_a_capped_listing_is_marked_incomplete(monkeypatch):
    monkeypatch.setattr(github, "MAX_FILES", 1)
    _github(monkeypatch, lambda request: httpx.Response(200, content=_tarball(FILES)))
    connector, docs = await _docs({"repo": "acme/handbook"})
    assert len(docs) == 1 and connector.complete is False  # nothing is pruned
