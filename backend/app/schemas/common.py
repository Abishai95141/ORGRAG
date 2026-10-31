import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.core.config import get_settings

Role = Literal["owner", "admin", "member", "viewer"]
Mode = Literal["quick", "deep"]
# "verified": the hidden source holding a collection's verified answers.
SourceKind = Literal["upload", "web", "github", "gdrive", "slack", "notion", "verified"]
SourceStatus = Literal["idle", "queued", "syncing", "ready", "error"]
RecencyBias = Literal["off", "low", "medium", "high"]
SourceOrigin = Literal["knowledge", "web", "attachment"]
Glyph = Literal[
    "stack", "book", "gear", "chart", "shield", "people", "code", "globe", "compass", "leaf"
]

# Input bounds. Names are trimmed before their length is checked, so "   " is rejected.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Description = Annotated[str, Field(max_length=2000)]
Slug = Annotated[str, Field(min_length=1, max_length=64)]
MAX_ID_LIST = 100
MAX_OFFSET = 10_000_000  # keeps ?offset= inside Postgres' bigint


class ErrorOut(BaseModel):
    """Every error response: a human message, a stable machine `code`, and the
    request id (also in the `X-Request-ID` header)."""

    detail: str
    code: str
    request_id: str | None


class Page[T](BaseModel):
    """Envelope for every paginated list: `limit`/`offset` echo the request."""

    items: list[T]
    total: int
    limit: int
    offset: int


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── users & auth ──────────────────────────────────────────────────────────────
class UserOut(Out):
    id: uuid.UUID
    email: str
    name: str
    avatar_hue: int
    created_at: datetime


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: Name


class LoginIn(BaseModel):
    email: EmailStr
    # Bounded so a huge body can't make the password hash expensive.
    password: str = Field(max_length=200)


class UserEnvelope(BaseModel):
    user: UserOut


# ── organizations ────────────────────────────────────────────────────────────
class OrgSettings(BaseModel):
    model: str = Field(default_factory=lambda: get_settings().default_model)
    quick_effort: Literal["low", "medium", "high"] = "low"
    deep_effort: Literal["medium", "high", "xhigh"] = "high"
    default_mode: Mode = "quick"
    retrieval_top_k: int = Field(default=8, ge=1, le=30)
    allow_cross_org: bool = True
    rerank: bool = True
    recency_bias: RecencyBias = "low"
    web_search: bool = False
    # Extract a knowledge graph from indexed documents (one LLM call per chunk) and give
    # the agent list/explore tools over it.
    knowledge_graph: bool = False


class OrgSettingsPatch(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=120)
    quick_effort: Literal["low", "medium", "high"] | None = None
    deep_effort: Literal["medium", "high", "xhigh"] | None = None
    default_mode: Mode | None = None
    retrieval_top_k: int | None = Field(default=None, ge=1, le=30)
    allow_cross_org: bool | None = None
    rerank: bool | None = None
    recency_bias: RecencyBias | None = None
    web_search: bool | None = None
    knowledge_graph: bool | None = None


class OrgSummary(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    hue: int
    role: Role
    member_count: int
    document_count: int


class OrgOut(OrgSummary):
    description: str | None
    settings: OrgSettings
    # The model runs actually use: settings.model, or the configured provider's
    # default when settings.model's provider has no API key. Null with no key at all.
    effective_model: str | None
    created_at: datetime


class OrgCreate(BaseModel):
    name: Name
    slug: str | None = Field(default=None, max_length=64)
    description: Description | None = None


class OrgPatch(BaseModel):
    name: Name | None = None
    description: Description | None = None
    hue: int | None = Field(default=None, ge=0, le=359)
    settings: OrgSettingsPatch | None = None


class MeOut(BaseModel):
    user: UserOut
    orgs: list[OrgSummary]


# ── members & invites ────────────────────────────────────────────────────────
class MemberOut(BaseModel):
    user: UserOut
    role: Role
    joined_at: datetime


class RolePatch(BaseModel):
    role: Role


class InviteCreate(BaseModel):
    email: EmailStr
    role: Role = "member"


class InviteOut(Out):
    id: uuid.UUID
    email: str
    role: Role
    token: str
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None


class InviteOrg(BaseModel):
    name: str
    slug: str
    hue: int


class InvitePreview(BaseModel):
    org: InviteOrg
    email: str
    role: Role
    expired: bool


# ── collections ──────────────────────────────────────────────────────────────
class CollectionOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str | None
    visibility: Literal["org", "restricted"]
    glyph: str
    document_count: int
    source_count: int
    allowed_user_ids: list[uuid.UUID]
    created_at: datetime


class CollectionCreate(BaseModel):
    name: Name
    description: Description | None = None
    visibility: Literal["org", "restricted"] = "org"
    glyph: Glyph = "stack"
    allowed_user_ids: list[uuid.UUID] = Field(default=[], max_length=1000)


class CollectionPatch(BaseModel):
    name: Name | None = None
    description: Description | None = None
    visibility: Literal["org", "restricted"] | None = None
    glyph: Glyph | None = None
    allowed_user_ids: list[uuid.UUID] | None = Field(default=None, max_length=1000)


# ── sources & documents ──────────────────────────────────────────────────────
class SourceProgress(BaseModel):
    done: int = 0
    total: int | None = None
    message: str | None = None


class SourceOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    collection_id: uuid.UUID
    collection_name: str
    kind: SourceKind
    name: str
    config: dict[str, Any]
    status: SourceStatus
    error: str | None
    document_count: int
    chunk_count: int
    sync_interval_minutes: int | None
    last_synced_at: datetime | None
    created_at: datetime
    progress: SourceProgress | None


SourceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
# 15 minutes .. 30 days
SyncInterval = Annotated[int, Field(ge=15, le=43200)]


class SourceCreate(BaseModel):
    kind: SourceKind
    name: SourceName
    collection_id: uuid.UUID
    config: dict[str, Any] = {}
    sync_interval_minutes: SyncInterval | None = None


class SourcePatch(BaseModel):
    name: SourceName | None = None
    config: dict[str, Any] | None = None
    sync_interval_minutes: SyncInterval | None = None


class UploadResult(BaseModel):
    accepted: int
    source: SourceOut


class DocumentRow(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    url: str | None
    mime: str
    chunk_count: int
    token_count: int
    boost: int
    hidden: bool
    feedback_up: int
    feedback_down: int
    source_updated_at: datetime | None
    updated_at: datetime


class DocumentDetail(DocumentRow):
    content: str
    my_vote: Literal["up", "down"] | None = None


DocumentPage = Page[DocumentRow]


class DocumentPatch(BaseModel):
    boost: int | None = Field(default=None, ge=-3, le=3)
    hidden: bool | None = None


class DocumentFeedbackIn(BaseModel):
    vote: Literal["up", "down"] | None
    message_id: uuid.UUID | None = None


class DocumentFeedbackOut(BaseModel):
    document_id: uuid.UUID
    vote: Literal["up", "down"] | None
    feedback_up: int
    feedback_down: int


class ConnectorInfo(BaseModel):
    kind: SourceKind
    name: str
    description: str
    config_schema: dict[str, Any]


# ── search ───────────────────────────────────────────────────────────────────
class SourceRef(BaseModel):
    """A citation target. `origin` says where it came from: the org knowledge base
    (all org/document fields set), the web (url + title only) or a file attached to
    the conversation (`attachment_id`)."""

    ref: int
    origin: SourceOrigin = "knowledge"
    document_id: uuid.UUID | None
    chunk_id: uuid.UUID | None
    attachment_id: uuid.UUID | None = None
    title: str
    url: str | None
    snippet: str
    org_id: uuid.UUID | None
    org_slug: str | None
    org_name: str | None
    org_hue: int | None
    collection_name: str | None
    source_kind: SourceKind | None
    score: float
    updated_at: datetime | None = None


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    org_slugs: list[Slug] | None = Field(default=None, max_length=20)
    collection_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_ID_LIST)
    limit: int = Field(default=10, ge=1, le=50)


class SearchOut(BaseModel):
    results: list[SourceRef]
    latency_ms: int


# ── agents ───────────────────────────────────────────────────────────────────
class AgentOut(Out):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str | None
    instructions: str
    mode: Mode
    glyph: str
    collection_ids: list[uuid.UUID]
    starter_prompts: list[str]
    created_by: uuid.UUID | None
    created_at: datetime


Instructions = Annotated[str, Field(max_length=20000)]
# Only the first six non-blank prompts are kept.
StarterPrompts = Annotated[list[Annotated[str, Field(max_length=500)]], Field(max_length=20)]


class AgentCreate(BaseModel):
    name: Name
    description: Description | None = None
    instructions: Instructions = ""
    mode: Mode = "quick"
    glyph: Glyph = "compass"
    collection_ids: list[uuid.UUID] = Field(default=[], max_length=MAX_ID_LIST)
    starter_prompts: StarterPrompts = []


class AgentPatch(BaseModel):
    name: Name | None = None
    description: Description | None = None
    instructions: Instructions | None = None
    mode: Mode | None = None
    glyph: Glyph | None = None
    collection_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_ID_LIST)
    starter_prompts: StarterPrompts | None = None


# ── chat ─────────────────────────────────────────────────────────────────────
class ConversationOut(Out):
    id: uuid.UUID
    org_id: uuid.UUID
    agent_id: uuid.UUID | None
    title: str
    scope_org_ids: list[uuid.UUID]
    mode: Mode
    created_at: datetime
    updated_at: datetime


ScopeSlugs = Annotated[list[Slug], Field(max_length=20)]


class ConversationCreate(BaseModel):
    agent_id: uuid.UUID | None = None
    mode: Mode | None = None
    scope_org_slugs: ScopeSlugs | None = None


class ConversationPatch(BaseModel):
    title: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
        | None
    ) = None
    mode: Mode | None = None
    scope_org_slugs: ScopeSlugs | None = None


ConversationPage = Page[ConversationOut]


class AttachmentOut(Out):
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    filename: str
    mime: str
    size_bytes: int
    token_count: int
    created_at: datetime


class AttachmentDetail(AttachmentOut):
    content: str


class MessageOut(Out):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    sources: list[dict[str, Any]]
    attachments: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    feedback: Literal["up", "down"] | None
    status: Literal["complete", "streaming", "error", "stopped"]
    created_at: datetime
    # Set when an admin verified this answer (conversation detail only).
    verified_answer_id: uuid.UUID | None = None
    # The active permalink token, if the answer is shared (conversation detail only).
    share_token: str | None = None


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class MessageCreate(BaseModel):
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)
    ]
    mode: Mode | None = None
    scope_org_slugs: ScopeSlugs | None = None
    collection_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_ID_LIST)
    attachment_ids: list[uuid.UUID] = Field(default=[], max_length=10)
    web_search: bool | None = None


class FeedbackIn(BaseModel):
    feedback: Literal["up", "down"] | None


# ── insights ─────────────────────────────────────────────────────────────────
class InsightTotals(BaseModel):
    questions: int
    conversations: int
    documents: int
    chunks: int
    active_users: int


class DayCount(BaseModel):
    date: str
    count: int


class TopSource(BaseModel):
    source_id: uuid.UUID
    name: str
    kind: SourceKind
    citations: int


class FeedbackCounts(BaseModel):
    up: int
    down: int


class RecentQuestion(BaseModel):
    query: str
    user_name: str
    created_at: datetime
    hits: int


class ModelUsage(BaseModel):
    """Tokens spent by answers in this org's conversations, per model."""

    model: str
    answers: int
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None  # None when the model has no per-token price


class InsightsOut(BaseModel):
    totals: InsightTotals
    questions_per_day: list[DayCount]
    top_sources: list[TopSource]
    feedback: FeedbackCounts
    recent_questions: list[RecentQuestion]
    usage: list[ModelUsage] = []


class HealthOut(BaseModel):
    status: str
    version: str
    llm_configured: bool
    llm_providers: list[str]
    default_model: str
    embedding_provider: str
    rerank_provider: str | None
    web_search_provider: str | None


class ModelOut(BaseModel):
    id: str
    label: str
    provider: Literal["anthropic", "nvidia", "explabs"]
    hint: str
    available: bool


# ── api keys ─────────────────────────────────────────────────────────────────
class ApiKeyOut(Out):
    id: uuid.UUID
    name: str
    prefix: str
    role: Role
    user_id: uuid.UUID
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class ApiKeyCreate(BaseModel):
    name: Name
    role: Literal["admin", "member", "viewer"] = "member"
    expires_in_days: int | None = Field(default=None, ge=1, le=730)


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="The secret key. Shown only once.")


# ── knowledge graph ──────────────────────────────────────────────────────────
class GraphStats(BaseModel):
    enabled: bool
    entities: int
    relations: int
    documents_extracted: int
    documents_pending: int


class EntityOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    description: str = Field(description="Assembled from the documents the caller can see")
    document_ids: list[uuid.UUID]


class EntityLink(BaseModel):
    entity: EntityOut
    keywords: str
    description: str


class EntityDetail(EntityOut):
    relations: list[EntityLink]
    relation_count: int


# ── verified answers ──────────────────────────────────────────────────────────
VerifiedStatus = Literal["active", "expiring", "expired"]


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    avatar_hue: int


class VerifiedAnswerOut(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    collection_name: str
    document_id: uuid.UUID
    message_id: uuid.UUID | None
    question: str
    answer: str
    sources: list[dict[str, Any]]
    owner: PersonOut | None
    verified_by: PersonOut | None
    verified_at: datetime
    expires_at: datetime | None
    # expiring = valid for 14 days or less; expired answers are left out of search.
    status: VerifiedStatus
    created_at: datetime
    updated_at: datetime


class VerifiedAnswerCreate(BaseModel):
    """From a chat answer (`message_id`: question, answer and sources default to it)
    or written directly (`question` and `answer`)."""

    message_id: uuid.UUID | None = None
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    answer: str | None = Field(default=None, min_length=1, max_length=50_000)
    collection_id: uuid.UUID
    owner_id: uuid.UUID | None = None  # default: the verifier
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)  # null: never expires


class VerifiedAnswerPatch(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    answer: str | None = Field(default=None, min_length=1, max_length=50_000)
    collection_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    expires_at: datetime | None = None  # explicit null: never expires


class ReverifyIn(BaseModel):
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)


# ── shared answers & access requests ──────────────────────────────────────────
class ShareOut(BaseModel):
    token: str
    url: str
    created_at: datetime


class SharedOrg(BaseModel):
    name: str
    slug: str
    hue: int


class MissingAccess(BaseModel):
    """Something the viewer would need to read to see a shared answer."""

    kind: Literal["org", "collection", "attachment", "hidden"]
    org_name: str
    org_slug: str | None = None  # only for orgs the viewer belongs to
    collection_id: uuid.UUID | None = None  # kind "collection" in an org the viewer is in
    collection_name: str | None = None
    requested: bool = False  # the viewer already has a pending request


class SharedAnswerOut(BaseModel):
    """`access: "full"`: the viewer can read every cited source, so they get the answer.
    `"restricted"`: they're in the org but can't read everything it cites (question and
    `missing` only). `"none"`: they aren't in the answer's org (org name only)."""

    token: str
    access: Literal["full", "restricted", "none"]
    org: SharedOrg
    author_name: str | None = None
    question: str | None = None
    answer: str | None = None
    sources: list[dict[str, Any]] = []
    missing: list[MissingAccess] = []
    answered_at: datetime | None = None
    shared_at: datetime


class AccessRequestIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


# ── Slack bot ─────────────────────────────────────────────────────────────────
class SlackOrgOut(BaseModel):
    """An org connected to a workspace, as the caller sees it (only orgs they're in)."""

    slug: str
    name: str
    hue: int
    role: Role  # the caller's role there


class SlackWorkspaceOut(BaseModel):
    id: uuid.UUID
    team_id: str
    team_name: str
    bot_user_id: str
    connected_at: datetime  # when this org connected it
    connected_by: PersonOut | None
    # The org unmapped channels search (the earliest connection); null when the caller
    # isn't a member of it.
    default_org: SlackOrgOut | None
    # Connected orgs the caller belongs to; the others are only counted.
    orgs: list[SlackOrgOut]
    other_org_count: int


class SlackOverview(BaseModel):
    configured: bool  # SLACK_CLIENT_ID, SLACK_CLIENT_SECRET and SLACK_SIGNING_SECRET set
    events_url: str  # for the Slack app's Event Subscriptions
    redirect_url: str  # for the Slack app's OAuth redirect URLs
    workspaces: list[SlackWorkspaceOut]


class SlackChannelOut(BaseModel):
    channel_id: str
    channel_name: str | None
    orgs: list[SlackOrgOut]  # in search order; only orgs the caller belongs to
    other_org_count: int
    updated_by: PersonOut | None
    updated_at: datetime


ChannelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class SlackChannelIn(BaseModel):
    # The orgs the channel searches, in order (the first the asker belongs to holds
    # the conversation). Each must have connected the workspace.
    org_slugs: list[Slug] = Field(min_length=1, max_length=20)
    channel_name: ChannelName | None = None


class SlackBotChannel(BaseModel):
    id: str
    name: str
    is_private: bool
    mapped: bool  # has a mapping that includes this org


class SlackLinkPreview(BaseModel):
    team_id: str
    team_name: str
    slack_user_id: str
    slack_user_name: str | None
    expired: bool
    # Who this Slack user is linked to now: nobody, the caller, or someone else.
    linked: Literal["none", "you", "other"]
    # The caller belongs to an org connected to the workspace (else they can't link).
    eligible: bool


class SlackLinkOut(BaseModel):
    id: uuid.UUID
    team_id: str
    team_name: str
    slack_user_id: str
    slack_user_name: str | None
    created_at: datetime


class AccessRequestOut(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    collection_name: str
    user: PersonOut
    note: str | None
    status: Literal["pending", "approved", "dismissed"]
    created_at: datetime
    decided_at: datetime | None
