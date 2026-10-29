import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["owner", "admin", "member", "viewer"]
Mode = Literal["quick", "deep"]
SourceKind = Literal["upload", "web", "github"]
SourceStatus = Literal["idle", "queued", "syncing", "ready", "error"]


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
    name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserEnvelope(BaseModel):
    user: UserOut


# ── organizations ────────────────────────────────────────────────────────────
class OrgSettings(BaseModel):
    model: str = "claude-opus-5-5"
    quick_effort: Literal["low", "medium", "high"] = "low"
    deep_effort: Literal["medium", "high", "xhigh"] = "high"
    default_mode: Mode = "quick"
    retrieval_top_k: int = Field(default=8, ge=1, le=30)
    allow_cross_org: bool = True


class OrgSettingsPatch(BaseModel):
    model: str | None = None
    quick_effort: Literal["low", "medium", "high"] | None = None
    deep_effort: Literal["medium", "high", "xhigh"] | None = None
    default_mode: Mode | None = None
    retrieval_top_k: int | None = Field(default=None, ge=1, le=30)
    allow_cross_org: bool | None = None


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
    created_at: datetime


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=64)
    description: str | None = None


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
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
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    visibility: Literal["org", "restricted"] = "org"
    glyph: str = "stack"
    allowed_user_ids: list[uuid.UUID] = []


class CollectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    visibility: Literal["org", "restricted"] | None = None
    glyph: str | None = None
    allowed_user_ids: list[uuid.UUID] | None = None


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


class SourceCreate(BaseModel):
    kind: SourceKind
    name: str = Field(min_length=1, max_length=160)
    collection_id: uuid.UUID
    config: dict[str, Any] = {}
    sync_interval_minutes: int | None = Field(default=None, ge=15)


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    config: dict[str, Any] | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=15)


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
    updated_at: datetime


class DocumentDetail(DocumentRow):
    content: str


class DocumentPage(BaseModel):
    items: list[DocumentRow]
    total: int


class ConnectorInfo(BaseModel):
    kind: SourceKind
    name: str
    description: str
    config_schema: dict[str, Any]


# ── search ───────────────────────────────────────────────────────────────────
class SourceRef(BaseModel):
    ref: int
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    title: str
    url: str | None
    snippet: str
    org_id: uuid.UUID
    org_slug: str
    org_name: str
    org_hue: int
    collection_name: str
    source_kind: SourceKind
    score: float


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    org_slugs: list[str] | None = None
    collection_ids: list[uuid.UUID] | None = None
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


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    instructions: str = ""
    mode: Mode = "quick"
    glyph: str = "compass"
    collection_ids: list[uuid.UUID] = []
    starter_prompts: list[str] = []


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    instructions: str | None = None
    mode: Mode | None = None
    glyph: str | None = None
    collection_ids: list[uuid.UUID] | None = None
    starter_prompts: list[str] | None = None


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


class ConversationCreate(BaseModel):
    agent_id: uuid.UUID | None = None
    mode: Mode | None = None
    scope_org_slugs: list[str] | None = None


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    mode: Mode | None = None
    scope_org_slugs: list[str] | None = None


class MessageOut(Out):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    sources: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    feedback: Literal["up", "down"] | None
    status: Literal["complete", "streaming", "error"]
    created_at: datetime


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    mode: Mode | None = None
    scope_org_slugs: list[str] | None = None
    collection_ids: list[uuid.UUID] | None = None


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


class InsightsOut(BaseModel):
    totals: InsightTotals
    questions_per_day: list[DayCount]
    top_sources: list[TopSource]
    feedback: FeedbackCounts
    recent_questions: list[RecentQuestion]


class HealthOut(BaseModel):
    status: str
    llm_configured: bool
    embedding_provider: str
