from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ConversationMemoryMessage(BaseModel):
    """Message recent utilise comme contexte, jamais comme preuve technique."""

    role: str
    content: str = ""
    timestamp: datetime | None = None
    referenced_document_ids: list[str] = Field(default_factory=list)
    intent: str | None = None
    entities: dict[str, Any] = Field(default_factory=dict)


class ConversationMemoryState(BaseModel):
    """Etat compact qui garde le sujet actif d'une conversation."""

    conversation_id: str
    active_document_ids: list[str] = Field(default_factory=list)
    active_topic: str | None = None
    active_entities: dict[str, Any] = Field(default_factory=dict)
    active_entity: dict[str, Any] | None = None
    previous_entity: dict[str, Any] | None = None
    entity_history: list[dict[str, Any]] = Field(default_factory=list)
    active_object: dict[str, Any] | None = None
    recall_cursor: int | None = None
    recent_entities: dict[str, list[str]] = Field(default_factory=dict)
    last_intent: str | None = None
    last_resolved_query: str | None = None
    last_evidence_refs: list[str] = Field(default_factory=list)
    summary: str | None = None
    updated_at: datetime | None = None


class ResolvedConversationQuery(BaseModel):
    """Question resolue avant classification documentaire."""

    original_query: str
    resolved_query: str
    query_type: str = "STANDALONE_DOCUMENT_QA"
    recall_answer: str | None = None
    recall_cursor: int | None = None
    inherited_entities: dict[str, Any] = Field(default_factory=dict)
    explicit_entities: dict[str, Any] = Field(default_factory=dict)
    resolution_confidence: float = 1.0
    used_memory: bool = False
    ambiguity_detected: bool = False
    ambiguity_reason: str | None = None
    recent_messages_count: int = 0
    summary_used: bool = False
    resolution_ms: int = 0

    @field_validator("resolution_confidence")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))
