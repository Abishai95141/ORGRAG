import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.connectors import gdrive
from app.connectors.base import ConnectorError
from app.connectors.gdrive import GoogleDriveConnector

FOLDER = "application/vnd.google-apps.folder"
DOC = "application/vnd.google-apps.document"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def secrets(rsa_key):
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    info = {
        "type": "service_account",
        "client_email": "strata@proj.iam.gserviceaccount.com",
        "private_key_id": "key-1",
        "private_key": pem,
    }
    return {"service_account_json": json.dumps(info)}


def _file(fid, name, mime, modified="2024-05-01T10:00:00.000Z", **extra):
    return {
        "id": fid,
        "name": name,
        "mimeType": mime,
        "modifiedTime": modified,
        "webViewLink": f"https://drive.google.com/file/d/{fid}/view",
        "owners": [{"displayName": "Ada", "emailAddress": "ada@acme.com"}],
        **extra,
    }


class FakeDrive:
    """Just enough of Drive v3: files.list (q parsing for parents / modifiedTime),
    files.get, alt=media downloads and exports, plus the OAuth token endpoint."""

    def __init__(self, files, children=None, contents=None):
        self.files = {f["id"]: f for f in files}
        self.children = children or {}
        self.contents = contents or {}
        self.assertions: list[str] = []
        self.list_calls: list[dict] = []
        self.token_response = httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            self.assertions.append(form["assertion"][0])
            return self.token_response
        assert request.headers["Authorization"] == "Bearer tok"
        path = request.url.path.removeprefix("/drive/v3")
        params = dict(request.url.params)
        if path == "/files":
            return self._list(params)
        if m := re.fullmatch(r"/files/([^/]+)/export", path):
            status, body = self.contents.get((m.group(1), params["mimeType"]), (404, b""))
            return httpx.Response(status, content=body)
        fid = path.removeprefix("/files/")
        if params.get("alt") == "media":
            status, body = self.contents.get((fid, "media"), (404, b""))
            return httpx.Response(status, content=body)
        if fid in self.files:
            return httpx.Response(200, json=self.files[fid])
        return httpx.Response(404, json={"error": {"code": 404, "message": "File not found"}})

    def _list(self, params):
        self.list_calls.append(params)
        q = params["q"]
        if m := re.match(r"'([^']+)' in parents", q):
            items = [self.files[i] for i in self.children.get(m.group(1), [])]
        else:
            items = [f for f in self.files.values() if f["mimeType"] != FOLDER]
        if m := re.search(r"modifiedTime > '([^']+)'", q):
            keep_folders = f"mimeType = '{FOLDER}' or" in q
            items = [
                f
                for f in items
                if f["modifiedTime"][:19] > m.group(1) or (keep_folders and f["mimeType"] == FOLDER)
            ]
        start = int(params.get("pageToken", 0))
        page = items[start : start + 2]
        if params["fields"] == "nextPageToken,files(id,mimeType)":
            page = [{"id": f["id"], "mimeType": f["mimeType"]} for f in page]
        body = {"files": page}
        if start + 2 < len(items):
            body["nextPageToken"] = str(start + 2)
        return httpx.Response(200, json=body)


@pytest.fixture
def fake(monkeypatch):
    def install(drive: FakeDrive) -> FakeDrive:
        monkeypatch.setattr(gdrive, "_transport", httpx.MockTransport(drive))
        return drive

    return install


async def _noop(*_):
    return None


async def _collect(conn, since=None):
    return [d async for d in conn.iter_documents(_noop, since)]


def test_validate_config():
    out = GoogleDriveConnector.validate_config(
        {
            "folder_ids": [
                "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing",
                "1AbCdEfGhIjKlMnOp",
                "",
                "https://drive.google.com/open?id=0BxYzAbCdEfGhIj",
            ],
            "include_shared_drives": "false",
            "impersonate_email": " ops@acme.com ",
            "service_account_json": "ignored here",
        }
    )
    assert out == {
        "folder_ids": ["1AbCdEfGhIjKlMnOp", "0BxYzAbCdEfGhIj"],
        "include_shared_drives": False,
        "impersonate_email": "ops@acme.com",
    }
    assert GoogleDriveConnector.validate_config({}) == {
        "folder_ids": [],
        "include_shared_drives": True,
    }
    assert GoogleDriveConnector.validate_config(
        {"folder_ids": "1AbCdEfGhIjKlMnOp, 1ZyXwVuTsRqPoNm"}
    )["folder_ids"] == ["1AbCdEfGhIjKlMnOp", "1ZyXwVuTsRqPoNm"]
    with pytest.raises(ValueError, match="folder"):
        GoogleDriveConnector.validate_config({"folder_ids": ["not a folder!"]})
    with pytest.raises(ValueError, match="email"):
        GoogleDriveConnector.validate_config({"impersonate_email": "ops"})
    assert GoogleDriveConnector.secret_fields() == ["service_account_json"]


async def test_full_sync_exports_and_downloads(fake, secrets):
    drive = fake(
        FakeDrive(
            files=[
                _file("doc1", "Handbook", DOC),
                _file("doc2", "Legacy notes", DOC),
                _file("sheet1", "Pricing", "application/vnd.google-apps.spreadsheet"),
                _file("deck1", "Roadmap", "application/vnd.google-apps.presentation"),
                _file("pdf1", "report.pdf", "application/pdf", size="2048"),
                _file("big1", "huge.pdf", "application/pdf", size=str(30 * 1024 * 1024)),
                _file("png1", "photo.png", "image/png", size="10"),
                _file("form1", "Survey", "application/vnd.google-apps.form"),
            ],
            contents={
                ("doc1", "text/markdown"): (200, b"# Handbook\n\nWelcome aboard."),
                ("doc2", "text/markdown"): (400, b"bad export"),
                ("doc2", "text/plain"): (200, b"Legacy plain text"),
                ("sheet1", "text/csv"): (200, b"plan,price\nGrowth,1200\n"),
                ("deck1", "text/plain"): (200, b"Q3 roadmap"),
                ("pdf1", "media"): (200, b"%PDF-1.4 fake"),
            },
        )
    )
    conn = GoogleDriveConnector({"impersonate_email": "ops@acme.com"}, secrets)
    docs = await _collect(conn)
    by_id = {d.external_id: d for d in docs}
    assert list(by_id) == ["doc1", "doc2", "sheet1", "deck1", "pdf1"]

    handbook = by_id["doc1"]
    assert handbook.title == "Handbook"
    assert handbook.mime == "text/markdown"
    assert handbook.data == b"# Handbook\n\nWelcome aboard."
    assert handbook.url == "https://drive.google.com/file/d/doc1/view"
    assert handbook.updated_at == datetime(2024, 5, 1, 10, tzinfo=UTC)
    assert handbook.metadata["owners"] == ["Ada"]
    assert handbook.metadata["mimeType"] == DOC

    assert (by_id["doc2"].mime, by_id["doc2"].data) == ("text/plain", b"Legacy plain text")
    assert by_id["sheet1"].mime == "text/csv"
    assert by_id["deck1"].mime == "text/plain"
    assert (by_id["pdf1"].mime, by_id["pdf1"].data) == ("application/pdf", b"%PDF-1.4 fake")

    call = drive.list_calls[0]
    assert call["q"] == f"trashed = false and mimeType != '{FOLDER}'"
    assert call["corpora"] == "allDrives" and call["supportsAllDrives"] == "true"
    assert call["includeItemsFromAllDrives"] == "true"
    assert len(drive.list_calls) == 4  # 8 files, 2 per page
    assert len(drive.assertions) == 1  # the access token is reused


async def test_jwt_assertion_is_rs256_with_expected_claims(fake, secrets, rsa_key):
    drive = fake(FakeDrive(files=[]))
    conn = GoogleDriveConnector({"impersonate_email": "ops@acme.com"}, secrets)
    assert await _collect(conn) == []
    token = drive.assertions[0]
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256" and header["kid"] == "key-1"
    claims = jwt.decode(
        token,
        rsa_key.public_key(),
        algorithms=["RS256"],
        audience="https://oauth2.googleapis.com/token",
    )
    assert claims["iss"] == "strata@proj.iam.gserviceaccount.com"
    assert claims["scope"] == "https://www.googleapis.com/auth/drive.readonly"
    assert claims["sub"] == "ops@acme.com"
    assert claims["exp"] - claims["iat"] == 3600

    no_sub = GoogleDriveConnector({}, secrets)
    info = json.loads(secrets["service_account_json"])
    assert "sub" not in jwt.decode(
        no_sub._assertion(info, 1_700_000_000),
        rsa_key.public_key(),
        algorithms=["RS256"],
        audience="https://oauth2.googleapis.com/token",
        options={"verify_exp": False, "verify_iat": False},
    )


def _tree() -> FakeDrive:
    return FakeDrive(
        files=[
            {"id": "rootFolder01", "name": "Policies", "mimeType": FOLDER},
            _file("sub1", "HR", FOLDER, modified="2023-01-01T00:00:00.000Z"),
            _file("old1", "old.md", "text/markdown", modified="2024-04-01T00:00:00.000Z"),
            _file("new1", "Leave policy", DOC, modified="2024-05-03T09:00:00.000Z"),
        ],
        children={"rootFolder01": ["old1", "sub1"], "sub1": ["new1"]},
        contents={
            ("old1", "media"): (200, b"# Old"),
            ("new1", "text/markdown"): (200, b"# Leave"),
        },
    )


async def test_folder_walk_full_and_incremental(fake, secrets):
    drive = fake(_tree())
    conn = GoogleDriveConnector(
        {"folder_ids": ["rootFolder01"], "include_shared_drives": False}, secrets
    )
    docs = await _collect(conn)
    assert [d.external_id for d in docs] == ["old1", "new1"]
    assert docs[0].metadata["path"] == "Policies/old.md"
    assert docs[1].metadata["path"] == "Policies/HR/Leave policy"
    assert "corpora" not in drive.list_calls[0]

    drive.list_calls.clear()
    since = datetime(2024, 5, 2, tzinfo=UTC)
    docs = await _collect(conn, since)
    assert [d.external_id for d in docs] == ["new1"]
    assert drive.list_calls[0]["q"] == (
        "'rootFolder01' in parents and trashed = false and "
        f"(mimeType = '{FOLDER}' or modifiedTime > '2024-05-02T00:00:00')"
    )

    everything = GoogleDriveConnector({}, secrets)
    drive.list_calls.clear()
    await _collect(everything, since)
    assert drive.list_calls[0]["q"].endswith("and modifiedTime > '2024-05-02T00:00:00'")


async def test_list_ids(fake, secrets):
    drive = fake(_tree())
    conn = GoogleDriveConnector({"folder_ids": ["rootFolder01"]}, secrets)
    assert await conn.list_ids() == {"old1", "new1"}
    assert all(c["fields"] == "nextPageToken,files(id,mimeType)" for c in drive.list_calls)


async def test_auth_errors(fake, secrets):
    drive = fake(FakeDrive(files=[]))
    with pytest.raises(ConnectorError, match="service account key"):
        await _collect(GoogleDriveConnector({}, {}))
    with pytest.raises(ConnectorError, match="client_email and private_key"):
        await _collect(GoogleDriveConnector({}, {"service_account_json": '{"type": "x"}'}))
    with pytest.raises(ConnectorError, match="not valid JSON"):
        await _collect(GoogleDriveConnector({}, {"service_account_json": "{nope"}))
    bad_key = json.dumps({"client_email": "a@b.c", "private_key": "-----BEGIN nonsense"})
    with pytest.raises(ConnectorError, match="PEM"):
        await _collect(GoogleDriveConnector({}, {"service_account_json": bad_key}))

    drive.token_response = httpx.Response(
        400, json={"error": "invalid_grant", "error_description": "Invalid JWT Signature."}
    )
    with pytest.raises(ConnectorError, match="invalid_grant.*Invalid JWT Signature"):
        await _collect(GoogleDriveConnector({}, secrets))

    drive.token_response = httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    with pytest.raises(ConnectorError, match="Folder missingFolder1 not found"):
        await _collect(GoogleDriveConnector({"folder_ids": ["missingFolder1"]}, secrets))
