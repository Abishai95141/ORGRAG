"""Google Drive connector: indexes Docs, Sheets, Slides and parseable files through the
Drive v3 REST API, authenticating as a service account (optionally impersonating a
Workspace user via domain-wide delegation)."""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import httpx
import jwt

from app.connectors.base import Connector, ConnectorError, ProgressFn, RawDocument
from app.ingestion.parsers import SUPPORTED_EXTENSIONS

_API = "https://www.googleapis.com/drive/v3"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_FOLDER = "application/vnd.google-apps.folder"
# Google-native formats have no bytes of their own; export them to something parseable.
# Docs try Markdown first and fall back to plain text if the export is refused.
_EXPORTS = {
    "application/vnd.google-apps.document": ("text/markdown", "text/plain"),
    "application/vnd.google-apps.spreadsheet": ("text/csv",),
    "application/vnd.google-apps.presentation": ("text/plain",),
}
_SUPPORTED_MIMES = set(SUPPORTED_EXTENSIONS.values())
_MAX_BYTES = 20 * 1024 * 1024
_FIELDS = (
    "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,size,"
    "owners(displayName,emailAddress))"
)
_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_FOLDER_URL = re.compile(r"/folders/([A-Za-z0-9_-]+)|[?&]id=([A-Za-z0-9_-]+)")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_TRIES = 5
# Tests swap in an httpx.MockTransport here.
_transport: httpx.AsyncBaseTransport | None = None


class GoogleDriveConnector(Connector):
    kind = "gdrive"
    display_name = "Google Drive"
    description = "Index Google Docs, Sheets, Slides and PDFs/Office files from Drive folders."
    supports_incremental = True
    config_schema = {
        "type": "object",
        "properties": {
            "service_account_json": {
                "type": "string",
                "title": "Service account key (JSON)",
                "format": "textarea",
                "secret": True,
            },
            "folder_ids": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Folder IDs or links (everything the account can see if empty)",
            },
            "include_shared_drives": {
                "type": "boolean",
                "title": "Include shared drives",
                "default": True,
            },
            "impersonate_email": {
                "type": "string",
                "format": "email",
                "title": "Impersonate user (domain-wide delegation, optional)",
            },
        },
    }

    def __init__(self, config: dict[str, Any], secrets: dict[str, Any]):
        super().__init__(config, secrets)
        self._token: str | None = None
        self._token_expires = 0.0

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        raw = config.get("folder_ids") or []
        if isinstance(raw, str):
            raw = re.split(r"[\s,]+", raw)
        if not isinstance(raw, list):
            raise ValueError("Folder IDs must be a list")
        folders: list[str] = []
        for item in raw:
            folder = _folder_id(str(item))
            if folder is None:
                raise ValueError(f"Not a Drive folder ID or link: {item}")
            if folder and folder not in folders:
                folders.append(folder)
        shared = config.get("include_shared_drives", True)
        if isinstance(shared, str):
            shared = shared.strip().lower() not in ("false", "0", "no", "off", "")
        out: dict[str, Any] = {"folder_ids": folders, "include_shared_drives": bool(shared)}
        if email := str(config.get("impersonate_email") or "").strip():
            if not _EMAIL.match(email):
                raise ValueError("Impersonate user must be an email address")
            out["impersonate_email"] = email
        return out

    # ---- auth -------------------------------------------------------------------

    def _service_account(self) -> dict[str, Any]:
        raw = self.secrets.get("service_account_json")
        if not raw:
            raise ConnectorError("Add the service account key (JSON) to this source")
        try:
            info = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError) as exc:
            raise ConnectorError("The service account key is not valid JSON") from exc
        if (
            not isinstance(info, dict)
            or not info.get("client_email")
            or not info.get("private_key")
        ):
            raise ConnectorError(
                "The service account key must contain client_email and private_key "
                "(download a JSON key for the service account in Google Cloud)"
            )
        return info

    def _assertion(self, info: dict[str, Any], now: int) -> str:
        """The signed JWT exchanged for an access token (OAuth 2.0 JWT bearer grant)."""
        claims = {
            "iss": info["client_email"],
            "scope": _SCOPE,
            "aud": _TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }
        if sub := self.config.get("impersonate_email"):
            claims["sub"] = sub
        headers = {"kid": info["private_key_id"]} if info.get("private_key_id") else None
        try:
            return jwt.encode(claims, info["private_key"], algorithm="RS256", headers=headers)
        except Exception as exc:  # cryptography/pyjwt raise several key-format errors
            raise ConnectorError(
                "The service account private_key is not a valid PEM RSA key"
            ) from exc

    async def _auth(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self._token is None or time.time() > self._token_expires - 60:
            now = int(time.time())
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": self._assertion(self._service_account(), now),
                },
            )
            if resp.status_code != 200:
                raise ConnectorError(_token_error(resp))
            body = resp.json()
            self._token = body["access_token"]
            self._token_expires = now + float(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=_API, timeout=60, transport=_transport)

    async def _get(self, client: httpx.AsyncClient, url: str, **params: Any) -> httpx.Response:
        """GET with auth, one token refresh on 401 and backoff on rate limits / 5xx."""
        resp: httpx.Response | None = None
        for attempt in range(_MAX_TRIES):
            resp = await client.get(url, params=params, headers=await self._auth(client))
            if resp.status_code == 401 and attempt == 0:
                self._token = None
                continue
            if resp.status_code == 429 or resp.status_code >= 500 or _rate_limited(resp):
                retry = resp.headers.get("Retry-After", "")
                await asyncio.sleep(float(retry) if retry.isdigit() else min(2**attempt, 30))
                continue
            return resp
        assert resp is not None
        return resp

    # ---- listing ----------------------------------------------------------------

    def _drive_params(self) -> dict[str, Any]:
        if self.config.get("include_shared_drives", True):
            return {
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "allDrives",
            }
        return {}

    async def _list(
        self, client: httpx.AsyncClient, query: str, fields: str = _FIELDS
    ) -> AsyncIterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            params = {"q": query, "fields": fields, "pageSize": 1000, **self._drive_params()}
            if page_token:
                params["pageToken"] = page_token
            resp = await self._get(client, "/files", **params)
            _check(resp, "listing files")
            body = resp.json()
            for item in body.get("files", []):
                yield item
            page_token = body.get("nextPageToken")
            if not page_token:
                return

    async def _files(
        self, client: httpx.AsyncClient, since: datetime | None, fields: str = _FIELDS
    ) -> AsyncIterator[tuple[dict[str, Any], list[str]]]:
        """Yield (file, folder path) for every non-folder file in scope (`fields` must
        include mimeType). With folders
        configured, walk them breadth-first; subfolders are always listed (a folder's
        modifiedTime doesn't change when something deep inside it does)."""
        changed = f" and modifiedTime > '{_rfc3339(since)}'" if since else ""
        folders = self.config.get("folder_ids") or []
        if not folders:
            query = f"trashed = false and mimeType != '{_FOLDER}'{changed}"
            async for item in self._list(client, query, fields):
                yield item, []
            return
        queue: list[tuple[str, list[str]]] = []
        for folder_id in folders:
            resp = await self._get(
                client, f"/files/{folder_id}", fields="id,name,mimeType", supportsAllDrives="true"
            )
            if resp.status_code == 404:
                raise ConnectorError(
                    f"Folder {folder_id} not found, or not shared with the service account"
                )
            _check(resp, f"reading folder {folder_id}")
            queue.append((folder_id, [resp.json().get("name", folder_id)]))
        seen: set[str] = set(folders)
        while queue:
            folder_id, path = queue.pop(0)
            query = f"'{folder_id}' in parents and trashed = false"
            if since:
                query += f" and (mimeType = '{_FOLDER}' or modifiedTime > '{_rfc3339(since)}')"
            async for item in self._list(client, query, fields):
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                if item.get("mimeType") == _FOLDER:
                    queue.append((item["id"], [*path, item.get("name", item["id"])]))
                else:
                    yield item, path

    # ---- sync -------------------------------------------------------------------

    async def iter_documents(
        self, progress: ProgressFn, since: datetime | None = None
    ) -> AsyncIterator[RawDocument]:
        self.config = self.validate_config(self.config)
        async with self._client() as client:
            await progress(0, None, "Listing files")
            files = [
                (item, path)
                async for item, path in self._files(client, since)
                if _plan(item) is not None and int(item.get("size") or 0) <= _MAX_BYTES
            ]
            for i, (item, path) in enumerate(files):
                await progress(i, len(files), item.get("name"))
                fetched = await self._content(client, item)
                if fetched is None:
                    continue
                data, mime = fetched
                metadata: dict[str, Any] = {"mimeType": item.get("mimeType")}
                if path:
                    metadata["path"] = "/".join([*path, item.get("name", "")])
                if owners := [
                    o.get("displayName") or o.get("emailAddress") for o in item.get("owners", [])
                ]:
                    metadata["owners"] = owners
                yield RawDocument(
                    external_id=item["id"],
                    title=item.get("name") or item["id"],
                    data=data,
                    mime=mime,
                    url=item.get("webViewLink"),
                    metadata=metadata,
                    updated_at=_parse_time(item.get("modifiedTime")),
                )
            await progress(len(files), len(files), None)

    async def _content(
        self, client: httpx.AsyncClient, item: dict[str, Any]
    ) -> tuple[bytes, str] | None:
        """Download or export one file; None when Drive refuses (too large, no access)."""
        plan = _plan(item)
        if plan is None:
            return None
        action, mimes = plan
        if action == "download":
            mime = mimes[0]
            resp = await self._get(
                client, f"/files/{item['id']}", alt="media", supportsAllDrives="true"
            )
        else:
            for mime in mimes:
                resp = await self._get(client, f"/files/{item['id']}/export", mimeType=mime)
                if resp.status_code == 200:
                    break
        if resp.status_code == 401:
            raise ConnectorError("Google rejected the access token")
        if resp.status_code != 200 or len(resp.content) > _MAX_BYTES:
            return None
        return resp.content, mime

    async def list_ids(self) -> set[str] | None:
        self.config = self.validate_config(self.config)
        async with self._client() as client:
            return {
                item["id"]
                async for item, _ in self._files(client, None, "nextPageToken,files(id,mimeType)")
            }


def _folder_id(value: str) -> str | None:
    """Accept a bare folder id or a Drive folder link. '' for blanks, None if invalid."""
    value = value.strip()
    if not value:
        return ""
    if "/" in value or "?" in value:
        m = _FOLDER_URL.search(value)
        value = (m.group(1) or m.group(2)) if m else ""
    return value if _ID.match(value) else None


def _plan(item: dict[str, Any]) -> tuple[str, tuple[str, ...]] | None:
    """How to fetch a file: ("export", mimes to try) or ("download", (mime,))."""
    mime = item.get("mimeType", "")
    if mime in _EXPORTS:
        return "export", _EXPORTS[mime]
    ext = PurePosixPath(str(item.get("name", "")).lower()).suffix
    if ext in SUPPORTED_EXTENSIONS:
        return "download", (SUPPORTED_EXTENSIONS[ext],)
    if mime in _SUPPORTED_MIMES:
        return "download", (mime,)
    return None


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _error(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code != 403:
        return False
    errors = (_error(resp).get("error") or {}).get("errors") or []
    return any(e.get("reason") in ("rateLimitExceeded", "userRateLimitExceeded") for e in errors)


def _check(resp: httpx.Response, what: str) -> None:
    if resp.status_code < 400:
        return
    err = _error(resp).get("error")
    message = err.get("message") if isinstance(err, dict) else None
    message = message or resp.text[:200]
    if resp.status_code == 401:
        raise ConnectorError("Google rejected the access token")
    raise ConnectorError(f"Google Drive error while {what} (HTTP {resp.status_code}): {message}")


def _token_error(resp: httpx.Response) -> str:
    body = _error(resp)
    code = body.get("error") or f"HTTP {resp.status_code}"
    detail = body.get("error_description") or resp.text[:200]
    msg = f"Google rejected the service account credentials ({code}): {detail}"
    if code == "unauthorized_client":
        msg += (
            ". Domain-wide delegation for this service account's client ID with the "
            "drive.readonly scope must be enabled in the Google Admin console"
        )
    return msg
