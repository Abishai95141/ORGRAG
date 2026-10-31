"""Request and response models for structured records (app/api/records.py)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

FieldType = Literal["text", "number", "date", "boolean", "choice"]
SchemaStatus = Literal["suggested", "active", "archived"]
Op = Literal["eq", "ne", "contains", "in", "lt", "lte", "gt", "gte", "exists", "missing"]
_NAME = r"^[a-z][a-z0-9_]{0,63}$"


class RecordField(BaseModel):
    name: str = Field(pattern=_NAME)
    label: str = Field(min_length=1, max_length=120)
    type: FieldType = "text"
    description: str = Field(default="", max_length=500)
    options: list[str] | None = None  # for "choice"
    aliases: list[str] = Field(default_factory=list, max_length=20)  # other column labels

    @model_validator(mode="after")
    def _choices(self):
        if self.type == "choice" and not self.options:
            raise ValueError(f"choice field '{self.name}' needs options")
        return self


def _check_fields(fields: list[RecordField], key_field: str) -> None:
    names = [f.name for f in fields]
    if len(set(names)) != len(names):
        raise ValueError("field names must be unique")
    if key_field not in names:
        raise ValueError(f"key_field '{key_field}' isn't one of the fields")


class RecordSchemaOut(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    collection_name: str
    name: str
    label: str
    description: str
    fields: list[dict[str, Any]]
    key_field: str
    origin: Literal["fields_table", "rows_table", "field_lines", "llm"]
    status: SchemaStatus
    stats: dict[str, Any]
    extracting: bool = False  # an extraction job is queued or running
    extracted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RecordFailure(BaseModel):
    document_id: uuid.UUID
    title: str
    error: str
    extracted_at: datetime | None


class RecordSchemaDetail(RecordSchemaOut):
    failures: list[RecordFailure] = Field(default_factory=list)


class RecordSchemaCreate(BaseModel):
    """A schema written by an admin; extracted from prose with LangExtract."""

    collection_id: uuid.UUID
    name: str = Field(pattern=_NAME)
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    fields: list[RecordField] = Field(min_length=1, max_length=40)
    key_field: str

    @model_validator(mode="after")
    def _valid(self):
        _check_fields(self.fields, self.key_field)
        return self


class RecordSchemaPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    fields: list[RecordField] | None = Field(default=None, min_length=1, max_length=40)
    key_field: str | None = None
    status: SchemaStatus | None = None


class RecordFilter(BaseModel):
    field: str
    op: Op = "eq"
    value: Any = None

    @field_validator("value")
    @classmethod
    def _plain(cls, v):
        if isinstance(v, dict):
            raise ValueError("value must be a string, number, boolean or list")
        return v


class RecordQueryIn(BaseModel):
    """Same shape as the agent's query_records tool."""

    schema_name: str = Field(alias="schema")
    # Only this schema (the Records page); by default every active schema with the name
    # in readable collections, as the agent queries.
    schema_id: uuid.UUID | None = None
    filters: list[RecordFilter] = Field(default_factory=list, max_length=12)
    mode: Literal["list", "count", "group"] = "list"
    group_by: str | None = None
    sort: str | None = None
    descending: bool = False
    limit: int = Field(default=100, ge=1, le=300)
    latest_only: bool = False

    model_config = {"populate_by_name": True}


class RecordDocument(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime | None


class RecordOut(BaseModel):
    id: uuid.UUID
    schema_id: uuid.UUID
    key: str
    values: dict[str, Any]
    provenance: dict[str, Any]
    document: RecordDocument


class RecordGroup(BaseModel):
    value: Any
    count: int


class RoleQueryIn(BaseModel):
    role: str = Field(min_length=1, max_length=200)
    of: str | None = Field(default=None, max_length=200)


class RoleMatchOut(BaseModel):
    record_type: str
    field: str  # the column named for the role, or the one whose value names it
    value: Any  # the holder (a role column) or the text naming the role
    by: Literal["column", "value"]
    record: "RecordOut"


class RecordQueryOut(BaseModel):
    total: int
    schemas: list[str]  # the schema ids queried (same name across collections)
    rows: list[RecordOut] = Field(default_factory=list)
    groups: list[RecordGroup] = Field(default_factory=list)
