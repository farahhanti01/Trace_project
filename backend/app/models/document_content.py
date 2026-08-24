from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


ContentType = Literal[
    "heading",
    "paragraph",
    "definition",
    "description",
    "rule",
    "procedure",
    "workflow",
    "field_description",
    "field_attribute",
    "field_usage",
    "valid_value",
    "code_mapping",
    "table",
    "table_row",
    "note",
    "warning",
    "example",
    "exception",
    "list",
    "message_type",
    "reference",
    "unknown",
]


class ContentHierarchy(BaseModel):
    chapter: str | None = None
    section: str | None = None
    subsection: str | None = None
    title: str | None = None


class SourceLocation(BaseModel):
    pdf_page: int | None = None
    printed_page: str | None = None
    sheet: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_index: int | None = None
    chunk_index: int | None = None


class ContentUnit(BaseModel):
    unit_id: str
    document_id: str
    conversation_id: str | None = None
    agent: str | None = None
    source: str | None = None
    hierarchy: ContentHierarchy = Field(default_factory=ContentHierarchy)
    document_zone: str | None = None
    content_type: ContentType = "unknown"
    entities: dict[str, Any] = Field(default_factory=dict)
    primary_entities: dict[str, Any] = Field(default_factory=dict)
    referenced_entities: dict[str, Any] = Field(default_factory=dict)
    entity_alignment: str | None = None
    content: str
    raw_text: str | None = None
    source_location: SourceLocation = Field(default_factory=SourceLocation)
    source_section_id: str | None = None
    source_chunk_id: str | None = None
    document_order: int | None = None
    parent_unit_id: str | None = None
    parsing_confidence: float = 1.0

    @field_validator("parsing_confidence")
    @classmethod
    def clamp_parsing_confidence(cls, value: float) -> float:
        if value < 0:
            return 0.0

        if value > 1:
            return 1.0

        return value


class EvidenceItem(BaseModel):
    content: str
    source_unit_id: str | None = None
    document_id: str | None = None
    pdf_page: int | None = None
    printed_page: str | None = None
    section: str | None = None
    content_type: str | None = None
    entity_alignment: str | None = None
    confidence: float = 1.0

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        if value < 0:
            return 0.0

        if value > 1:
            return 1.0

        return value


class EvidenceTable(BaseModel):
    title: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    source_unit_ids: list[str] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    query: str
    intent: str
    entities: dict[str, Any] = Field(default_factory=dict)
    target_entities: dict[str, Any] = Field(default_factory=dict)
    definitions: list[EvidenceItem] = Field(default_factory=list)
    facts: list[EvidenceItem] = Field(default_factory=list)
    rules: list[EvidenceItem] = Field(default_factory=list)
    tables: list[EvidenceTable] = Field(default_factory=list)
    examples: list[EvidenceItem] = Field(default_factory=list)
    process_steps: list[EvidenceItem] = Field(default_factory=list)
    other_evidence: list[EvidenceItem] = Field(default_factory=list)
    retrieved_units: list[ContentUnit] = Field(default_factory=list)
    citations: list[EvidenceItem] = Field(default_factory=list)
