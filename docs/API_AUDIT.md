# API hygiene audit

Scope: the HTTP layer (`backend/app/main.py`, `app/core`, `app/api`, `app/schemas`) against
`docs/API.md`, judged from the point of view of a third-party developer using an API key
and of the web app. Evidence line numbers refer to commit `6fdb7ce` (before the fixes).
Regression tests for every fixed behaviour are in `backend/tests/test_api_hygiene.py`.

**Totals:** 4 high, 14 medium, 22 low. Fixed: 4 high, 12 medium, 17 low. Deferred: 2 medium,
5 low (reasons below).

## Findings

| # | Sev | Area | Finding | Evidence | Status |
|---|---|---|---|---|---|
| 1 | high | Authz / API keys | An org-bound API key could read **other orgs** its creator belongs to: `POST /search` passed `org_slugs` through, `POST /conversations` stored any member org in `scope_org_slugs`, and a key could post to a conversation the session had widened. Retrieval resolves scope from the user's memberships, not the key. | `api/search.py:20`, `api/chat.py:58-68`, `api/chat.py:288`, `retrieval/access.py` `resolve_scope` | **Fixed.** Keys search/chat only their own org; other slugs are ignored (`api/search.py`, `api/chat.py` `_scope_ids`, `send_message`). Tests `test_api_key_search_stays_in_its_org_and_role`, `test_api_key_chat_is_bound_to_its_org`. |
| 2 | high | Authz / API keys | The key's **role cap was ignored by retrieval**: a `viewer` key created by an admin read restricted collections through `/search` and chat (retrieval uses the membership role), while `GET /documents` with the same key hid them. | `api/search.py:16-24`, `retrieval/access.py` (`select(Organization, Membership.role)`) | **Fixed** at the API boundary: `api/scope.py::key_collection_ids` passes an explicit collection allow-list computed with the capped role (agent collection filter folded in). Same tests as #1. |
| 3 | high | Authz / API keys | Any API key, **even a viewer one, could remove its creator from the org** (self-removal is allowed for every role), so a leaked read-only key could sign an admin/owner out. | `api/orgs.py:161-173` | **Fixed.** Self-removal with an API key is 403. Test `test_api_key_cannot_remove_its_own_user`. |
| 4 | high | Input / DoS | **No request body cap**, and uploads were read fully into memory before the size check (`await upload.read()` then `len(data) > limit`); multipart batches had no file-count or total limit. Any member (attachments: any role) could exhaust memory/disk. | `api/sources.py:180-188`, `api/attachments.py:41-46`, `main.py` (no limit) | **Fixed.** `core/http.py::BodyLimit` (JSON ≤ 1 MB, multipart ≤ 100 MB, Content-Length and streamed bytes) → 413 `request_too_large`; per-file size checked from `UploadFile.size` before reading; ≤ 20 files per upload (`too_many_files`). Tests `test_error_envelope_for_framework_errors`, `test_streamed_bodies_over_the_cap_are_413`, `test_upload_batches_are_checked_before_anything_is_stored`. |
| 5 | medium | Cross-user ids | Document feedback accepted **any `message_id`** (any org, any user): it linked a vote to someone else's message and was an existence oracle for message ids across orgs. | `api/documents.py:157-160` | **Fixed.** Must be a message in one of the caller's own conversations in this org, else 404. Test `test_document_feedback_message_must_be_the_callers`. |
| 6 | medium | Authz / API keys | `GET /auth/me` with an API key listed **all** the creator's orgs (names, slugs, member and document counts). | `api/auth.py:76-78` | **Fixed.** With a key, `orgs` is just the key's org. Test `test_api_key_me_lists_only_its_org`. |
| 7 | medium | Authz | Removing a member left their **API keys** (they then got a confusing 404 instead of 401) and their **restricted-collection grants** (silently restored if re-invited). | `api/orgs.py:161-173` | **Fixed.** Both are deleted with the membership. Test `test_removing_a_member_revokes_their_keys_and_grants`. |
| 8 | medium | Input / DoS | `LoginIn.password` was unbounded, so one request could make the Argon2 verification arbitrarily expensive. | `schemas/common.py:45-47` | **Fixed** (`max_length=200`, as signup). Test `test_out_of_range_input_is_422_not_500`. |
| 9 | medium | Integrity | Races surfaced as **500**: concurrent signups with one email (reproduced: 2 of 4 → 500), concurrent creates with one slug, concurrent votes by one person (unique `(document_id, user_id)` violation, and recounted totals could go stale), double-accepting an invite (membership PK). | `api/auth.py:38-47`, `api/orgs.py:50-71`, `api/documents.py:161-195`, `api/orgs.py:265-266` | **Fixed.** 409 `email_taken` / `slug_taken` on `IntegrityError`; votes take a row lock on the document (serialises the vote and the recount); accept uses `INSERT … ON CONFLICT DO NOTHING`. Plus a global `IntegrityError` → 409 `conflict` backstop in `main.py`. Tests `test_concurrent_writes_conflict_instead_of_failing`, `test_concurrent_votes_keep_one_vote_and_true_totals`, `test_double_accepting_an_invite_does_not_fail`, `test_unhandled_and_integrity_errors_keep_the_envelope_and_cors`. |
| 10 | medium | Input | Out-of-range input became **500 instead of 422** (reproduced): agent `glyph` > 32 chars (`VARCHAR(32)`), `sync_interval_minutes` > int32, `?offset=` > int64 on documents/conversations/source documents. | `schemas/common.py:215,221,334,344`; `api/documents.py:105`, `api/chat.py:82`, `api/sources.py:202` | **Fixed.** Glyph enum, interval 15–43200, `offset ≤ 10 000 000`. Test `test_out_of_range_input_is_422_not_500`. |
| 11 | medium | Input | Unbounded strings and lists: descriptions, agent instructions, starter prompts (count and length), `collection_ids`, `allowed_user_ids`, `org_slugs`, `scope_org_slugs`. | `schemas/common.py` (`CollectionCreate`, `AgentCreate`, `SearchIn`, `ConversationCreate`, `MessageCreate`, `OrgCreate`) | **Fixed.** Bounds listed in `docs/API.md` → *Limits*. |
| 12 | medium | Secrets | DB errors are logged with **their bound parameters** (`log.exception` on any 500 prints SQLAlchemy's `[parameters: …]`), which include connector `secrets`, API-key hashes and document text. Reproduced with an oversized insert. | `core/db.py:13`, `main.py:102-105` | **Fixed.** `create_async_engine(..., hide_parameters=True)`. |
| 13 | medium | Consistency | `GET /conversations` is an unbounded, `limit`/`offset`-paged list but returned a plain array (no `total`), against the `Page` rule. | `api/chat.py:78-91`, `docs/API.md` | **Fixed (contract change).** Returns `Page<Conversation>`; docs, `web/src/lib/api.ts` updated (the client unwraps `items`, callers unchanged). Test `test_conversation_list_is_a_page`. |
| 14 | medium | Performance | List endpoints aggregated **whole tables**: every documents page grouped all chunks in the database; `org_summaries` (`/auth/me`, `/orgs`, every `Org` response) counted memberships and documents for every tenant. | `api/documents.py:65-70`, `api/serializers.py:32-37` | **Fixed.** Chunk counts only for the page's ids; summary counts only for the caller's orgs. Covered by the existing documents/orgs tests. |
| 15 | medium | Correctness | `description: null` could not clear an org or collection description (ignored), although the web app sends `null` to clear it. | `api/orgs.py:96-97`, `api/collections.py:77-78`; `web/src/app/o/[org]/settings/page.tsx:104`, `collections/page.tsx:141` | **Fixed** (`model_fields_set`). Test `test_descriptions_can_be_cleared`. |
| 16 | medium | OpenAPI | `/api/docs` was hard to use from outside: no security scheme (no *Authorize*), the real error envelope undocumented (FastAPI's `HTTPValidationError` shown for 422), operation ids like `list_documents_api_orgs__slug__documents_get` and duplicate function names across modules. | `main.py:41-47` | **Fixed.** `bearer` scheme via `HTTPBearer(auto_error=False)` in `current_user`; `default` → `ErrorOut` on every operation; operation ids `<tag>-<function>`; docstrings on non-obvious operations. Test `test_openapi_is_usable_by_third_parties`. |
| 17 | medium | Abuse | No rate limiting beyond login: signup, invite-token preview, and per-key search/chat volume are unlimited. | `api/auth.py`, `api/orgs.py:244`, `api/search.py`, `api/chat.py` | **Deferred.** The in-process limiter is per replica, and the limits are a product/infra decision; belongs at the proxy or in a shared store. |
| 18 | medium | Auth | Session JWTs can't be revoked: logout only clears the cookie, and a copied bearer JWT stays valid for 14 days (also after a password change). | `core/security.py:26-38`, `api/auth.py:69-73` | **Deferred.** Needs a per-user token version (new column + migration), outside the allowed model changes (constraints/indexes only). |
| 19 | low | Errors | 500 responses were produced by Starlette's outermost error middleware, **outside CORS**, so a browser on another origin couldn't read the envelope. | `main.py:50-66,102-105` | **Fixed.** CORS is now the outermost middleware and the request-id middleware turns unhandled exceptions into the envelope. Test `test_unhandled_and_integrity_errors_keep_the_envelope_and_cors`. |
| 20 | low | Errors | Framework errors got the generic code `error` (e.g. 400 "Invalid multipart data", future 413/409/500 `HTTPException`s); invalid JSON read `"1: JSON decode error"`. | `main.py:80-99` | **Fixed.** Status → code map (`bad_request`, `conflict`, `request_too_large`, …) and a plain "not valid JSON" message. Test `test_error_envelope_for_framework_errors`. |
| 21 | low | Errors | 405 `Allow` named only the first matching route's method (`PUT /orgs/x` → `Allow: GET`, though PATCH and DELETE exist). | Starlette routing; `main.py:78-91` | **Fixed** (walks all routes). Same test. |
| 22 | low | Consistency | A trailing slash got a **307 to an absolute backend URL**, which breaks behind the Next.js rewrite and makes some clients drop `Authorization`. | FastAPI `redirect_slashes` | **Fixed.** `core/http.py::StripTrailingSlash` routes `/x/` as `/x`. Test `test_trailing_slashes_route_without_redirects`. |
| 23 | low | Errors | Any `X-Request-ID` (any bytes, 64 chars) was echoed and logged. | `main.py:62-63` | **Fixed.** Only `[A-Za-z0-9._:-]{1,64}` is echoed; otherwise one is generated. |
| 24 | low | Input | Whitespace-only names passed `min_length=1` and were stored as `""` (stripped after validation). | `schemas/common.py` names; `api/*` `.strip()` | **Fixed.** Names are stripped before validation (422). |
| 25 | low | Input | Collections silently replaced an unknown `glyph` with `stack`; agents stored any string. | `api/collections.py:57,81`, `schemas/common.py:334` | **Fixed.** One `Glyph` enum for both (422). `web/src/lib/types.ts` `Glyph` gained `compass`, `leaf`. |
| 26 | low | Input | `settings.model` accepted any string; a typo only failed at answer time (502 `llm_model`). | `api/orgs.py:100-103` | **Fixed.** Must be a catalogue id or the current/default model, else 422 `unknown_model`. |
| 27 | low | Input | `q` on document lists treated `%` and `_` as wildcards. | `api/documents.py:59-61` | **Fixed** (escaped). Test `test_document_search_treats_wildcards_literally`. |
| 28 | low | Integrity | Upload batches were written file by file: a bad 3rd file returned 400 after the first two were stored (with no sync queued). | `api/sources.py:180-190` | **Fixed.** Whole batch validated first. Test `test_upload_batches_are_checked_before_anything_is_stored`. |
| 29 | low | Integrity | Source/document deletes removed files **before** the DB commit (a failed commit left rows without files); the document file path joined `external_id` unchecked. | `api/sources.py:145-148`, `api/documents.py:207-211` | **Fixed.** Commit first; the path must stay inside the source's folder. |
| 30 | low | Consistency | `GET /connectors` needed no auth while `GET /models` did. | `api/sources.py:32-33` | **Fixed** (signed in). Test `test_connector_catalogue_needs_auth`. |
| 31 | low | Consistency | `PATCH /documents/{id}` returned `DocumentDetail` with `my_vote` always null. | `api/documents.py:145-147` | **Fixed.** |
| 32 | low | Input | Chat attachment names kept Windows paths and control characters. | `api/attachments.py:36` | **Fixed.** |
| 33 | low | Authz | Nothing enforced that every `/api/orgs/{slug}/…` route goes through `org_context` — the only thing binding an API key to its org (the key check in `current_user` is a path-prefix allow). | `core/deps.py:51-53` | **Fixed** by a structural test, `test_every_org_route_goes_through_the_org_context`. |
| 34 | low | Docs | `docs/API.md` didn't state the 404-not-403 rule, the error codes, limits or slash behaviour. | `docs/API.md` | **Fixed.** |
| 35 | low | Docs | `sync`/`files` and other non-obvious operations had no description in OpenAPI. | `api/*.py` | **Fixed** (docstrings). |
| 36 | low | OpenAPI | `Message.sources/attachments/trace` are `dict[str, Any]` in the schema, so generated clients get untyped citations and traces. | `schemas/common.py:392-394` | **Deferred.** `MessageOut` also serialises the agent's live payloads (`agent/engine.py::message_payload`, out of scope) and stored JSON rows; typing it re-validates both. Coordinate with the agent work. |
| 37 | low | Errors | 422 reports only the first validation error. | `main.py:94-99` | **Deferred.** Envelope stays fixed; an additive `errors` array can come later if clients need it. |
| 38 | low | Consistency | `GET /conversations/{id}` returns every message unpaged. | `api/chat.py:115-124` | **Deferred.** Bounded per conversation; paging needs UI work. |
| 39 | low | Integrity | Pending invites aren't unique per `(org, email)`; two concurrent invites create two valid links. | `api/orgs.py:201-217` | **Deferred.** Harmless; a partial unique index isn't worth a migration now. |
| 40 | low | Status codes | `POST /sources/{id}/sync` and `/files` return 200 for work that is only queued (202 would be precise). | `api/sources.py:152,168` | **Deferred.** Changing the status breaks clients that check `== 200`; behaviour is documented. |

Not findings (checked and fine): ids from another org on every `/{id}` route (404 via
`org_id` checks), restricted documents/sources (404 via `visible_collections`),
attachments and conversations of another user (404), message feedback on another user's
message (404), agent `collection_ids` from another org (400 `bad_collections`),
`allowed_user_ids` naming non-members (400 `not_members`), connector secrets never in
responses (`_public_config`), API keys shown once and stored hashed, role requirements
matching `docs/API.md`, 201/204 on create/delete, `collections_out`/`sources_out`
batched (no N+1). Login timing reveals whether an email is registered, but signup's
409 `email_taken` reveals that by design, so it isn't treated as a leak. Admin API keys can
read invite tokens — that is the admin role's documented capability.

Follow-up for the retrieval owner: #2 is fixed at the API boundary. Moving the role cap
into `retrieval/access.resolve_scope` (e.g. a `max_role` argument) would make it hold for
any future caller too.

## Contract changes

- `GET /orgs/{slug}/conversations` → `Page<Conversation>` (was `Conversation[]`). Web
  client updated.
- New 4xx where the API used to accept or 500: 413 `request_too_large`, 400
  `too_many_files`, 422 for out-of-range input, unknown glyphs and blank names, 422
  `unknown_model`, 403 for API-key self-removal, 404 for a foreign `message_id`.
- `GET /connectors` needs a signed-in caller.
- `/auth/me`, search and chat are narrower with API keys (see #1, #2, #6).
- `description: null` now clears org/collection descriptions.
- Trailing slashes route directly instead of 307.
- OpenAPI operation ids changed to `<tag>-<function>`.
- `web/src/lib/api.ts` `uploadFiles` sends batches of ≤ 20 files / 90 MB to stay within
  the new caps; its signature and result are unchanged.
