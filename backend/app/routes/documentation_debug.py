import time
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import (
    documents_collection,
)
from app.services.adaptive_retrieval_service import AdaptiveRetriever
from app.services.documentation_agent_service import (
    answer_documentation_question,
    load_sections,
)
from app.services.documentation_evidence_generation_service import (
    build_evidence_candidate,
)
from app.services.evidence_builder_service import (
    EvidenceBuilder,
)
from app.services.conversation_memory_service import (
    resolve_documentation_query,
)
from app.services.generic_question_classifier_service import (
    ClassificationResult,
    GenericQuestionClassifier,
    QueryPlanner,
)


router = APIRouter(
    prefix="/api/documentation/debug",
    tags=["Documentation Debug"],
)


class EvidenceDebugRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: str | None = None
    referenced_document_ids: list[str] = Field(default_factory=list)
    include_all_agents: bool = True
    chunks_limit: int = 8
    units_limit: int = 40
    evidence_limit: int = 40
    full: bool = False


class CompareGenerationRequest(EvidenceDebugRequest):
    pass


def valid_object_ids(values: list[str]) -> list[ObjectId]:
    return [
        ObjectId(value)
        for value in values
        if ObjectId.is_valid(value)
    ]


async def document_ids_for_debug(
    request: EvidenceDebugRequest,
) -> list[str]:
    object_ids = valid_object_ids(request.referenced_document_ids)

    if object_ids:
        return [
            str(document["_id"])
            for document in await documents_collection.find(
                {
                    "_id": {"$in": object_ids},
                    "status": "extracted",
                }
            ).to_list(length=100)
        ]

    if not request.conversation_id:
        return []

    query: dict[str, Any] = {
        "conversation_id": request.conversation_id,
        "status": "extracted",
    }

    if not request.include_all_agents:
        query["agent"] = "documentation"

    return [
        str(document["_id"])
        for document in await documents_collection.find(query).to_list(length=100)
    ]


def trim_chunk(
    section: dict[str, Any],
    full: bool,
) -> dict[str, Any]:
    text = section.get("text") or ""

    return {
        "source_id": section.get("source_id"),
        "source": section.get("source"),
        "page": section.get("page"),
        "printed_page": section.get("page_document"),
        "heading": section.get("heading"),
        "field_number": section.get("field_number"),
        "content_type": section.get("content_type"),
        "section_index": section.get("section_index"),
        "chunk_index": section.get("chunk_index"),
        "adaptive_score": section.get("adaptive_score"),
        "adaptive_reason": section.get("adaptive_reason"),
        "text": text if full else f"{text[:800]}..." if len(text) > 800 else text,
    }


def limit_evidence_bundle(
    bundle: dict[str, Any],
    limit: int,
    full: bool,
) -> dict[str, Any]:
    if full:
        return bundle

    limited = dict(bundle)

    for key in (
        "definitions",
        "facts",
        "rules",
        "examples",
        "process_steps",
        "other_evidence",
        "retrieved_units",
        "citations",
    ):
        value = limited.get(key)

        if isinstance(value, list):
            limited[key] = value[:limit]

    if isinstance(limited.get("tables"), list):
        limited["tables"] = limited["tables"][:limit]

    return limited


def flatten_entities_for_evidence(
    classification: ClassificationResult,
) -> dict[str, Any]:
    if classification.intent == "COMPARISON":
        return {}

    entities = classification.entities
    flattened: dict[str, Any] = {}

    if entities.field_numbers:
        flattened["field_number"] = entities.field_numbers[0]

    if entities.codes:
        flattened["code"] = entities.codes[0]

    if entities.message_types:
        flattened["message_type"] = entities.message_types[0]

    return flattened


@router.post("/evidence")
async def debug_evidence(
    request: EvidenceDebugRequest,
):
    if not request.conversation_id and not request.referenced_document_ids:
        raise HTTPException(
            status_code=400,
            detail="conversation_id or referenced_document_ids is required.",
        )

    sections = await load_sections(
        request.conversation_id or "",
        referenced_document_ids=request.referenced_document_ids,
        include_all_agents=request.include_all_agents,
    )
    document_ids = await document_ids_for_debug(request)
    memory_resolution = None
    memory_state = None
    recent_messages = []
    effective_question = request.question

    if request.conversation_id:
        try:
            memory_resolution, memory_state, recent_messages = await resolve_documentation_query(
                question=request.question,
                conversation_id=request.conversation_id,
            )
            effective_question = memory_resolution.resolved_query
        except Exception:
            memory_resolution = None

    classification = GenericQuestionClassifier.classify(effective_question)
    query_plan = QueryPlanner.build(classification)
    retrieval = await AdaptiveRetriever.retrieve(
        question=effective_question,
        sections=sections,
        document_ids=document_ids,
        plan=query_plan,
        sections_limit=request.chunks_limit,
        units_limit=(
            1_000
            if request.full
            else None
            if query_plan.require_complete_table or query_plan.require_hierarchy
            else request.units_limit
        ),
    )
    selected_sections = [
        {
            **section,
            "source_id": f"S{index}",
        }
        for index, section in enumerate(retrieval.sections, start=1)
    ]
    content_units = retrieval.content_units
    evidence_entities = flatten_entities_for_evidence(classification)
    bundle = EvidenceBuilder.build(
        question=effective_question,
        intent=classification.intent,
        entities=evidence_entities,
        retrieved_sections=selected_sections,
        content_units=content_units,
        limit=None
        if request.full or query_plan.require_complete_table
        else request.evidence_limit,
    )

    return {
        "question": request.question,
        "resolved_question": effective_question,
        "memory": {
            "state": memory_state.model_dump() if memory_state else None,
            "recent_messages": [
                message.model_dump()
                for message in recent_messages
            ][: request.units_limit if not request.full else 1_000],
            "resolution": (
                memory_resolution.model_dump()
                if memory_resolution
                else None
            ),
            "inherited_entities": (
                memory_resolution.inherited_entities
                if memory_resolution
                else {}
            ),
            "explicit_entities": (
                memory_resolution.explicit_entities
                if memory_resolution
                else {}
            ),
        },
        "classification": classification.model_dump(),
        "intent": classification.intent,
        "entities": classification.entities.model_dump(),
        "query_plan": query_plan.model_dump(),
        "retrieval_strategies_used": retrieval.strategies_used,
        "retrieved_chunks": [
            trim_chunk(section, full=request.full)
            for section in selected_sections[: request.chunks_limit]
        ],
        "text_sections": [
            trim_chunk(section, full=request.full)
            for section in selected_sections[: request.chunks_limit]
        ],
        "content_units": content_units
        if request.full
        else content_units[: request.units_limit],
        "expansions": retrieval.expansions,
        "completeness": retrieval.completeness.model_dump(),
        "evidence_bundle": limit_evidence_bundle(
            bundle.model_dump(),
            limit=request.evidence_limit,
            full=request.full,
        ),
    }


@router.post("/compare-generation")
async def debug_compare_generation(
    request: CompareGenerationRequest,
):
    if not request.conversation_id:
        raise HTTPException(
            status_code=400,
            detail="conversation_id is required to compare the legacy pipeline.",
        )

    sections = await load_sections(
        request.conversation_id,
        referenced_document_ids=request.referenced_document_ids,
        include_all_agents=request.include_all_agents,
    )
    memory_resolution = None
    memory_state = None
    recent_messages = []

    try:
        memory_resolution, memory_state, recent_messages = await resolve_documentation_query(
            question=request.question,
            conversation_id=request.conversation_id,
        )
    except Exception:
        memory_resolution = None

    old_started = time.perf_counter()
    old_response = await answer_documentation_question(
        question=request.question,
        conversation_id=request.conversation_id,
        referenced_document_ids=request.referenced_document_ids,
        include_all_agents=request.include_all_agents,
        force_legacy=True,
    )
    old_latency_ms = round((time.perf_counter() - old_started) * 1000)

    candidate = None
    evidence_error = None

    try:
        candidate = await build_evidence_candidate(
            question=(
                memory_resolution.resolved_query
                if memory_resolution
                else request.question
            ),
            sections=sections,
            chunks_limit=request.chunks_limit,
            units_limit=(
                1_000
                if request.full
                else None
            ),
            original_question=request.question,
            conversation_id=request.conversation_id,
            memory_resolution=memory_resolution,
        )
    except Exception as error:
        evidence_error = str(error)

    return {
        "question": request.question,
        "resolved_question": (
            candidate.get("resolved_question")
            if candidate
            else (
                memory_resolution.resolved_query
                if memory_resolution
                else request.question
            )
        ),
        "memory": {
            "state": memory_state.model_dump() if memory_state else None,
            "recent_messages_count": len(recent_messages),
            "resolution": (
                memory_resolution.model_dump()
                if memory_resolution
                else None
            ),
        },
        "classification": (
            candidate["classification"].model_dump()
            if candidate
            else None
        ),
        "answer_requirements": (
            candidate["answer_requirements"].model_dump()
            if candidate
            else None
        ),
        "old_pipeline": {
            "response": old_response,
            "latency_ms": old_latency_ms,
        },
        "evidence_pipeline": {
            "supported": candidate["supported"] if candidate else False,
            "response": candidate["response"] if candidate else None,
            "latency_ms": (
                candidate["latency"].total_evidence_ms
                if candidate
                else 0
            ),
            "latency": (
                candidate["latency"].model_dump()
                if candidate
                else None
            ),
            "validation": (
                candidate["validation"].model_dump()
                if candidate
                else {
                    "valid": False,
                    "missing_requirements": [],
                    "fact_mismatches": [],
                    "unsupported_claims": [evidence_error],
                }
            ),
            "diagnostics": (
                candidate["diagnostics"]
                if candidate
                else None
            ),
            "candidate_valid": (
                candidate["candidate_valid"]
                if candidate
                else False
            ),
            "fallback_reason": (
                candidate["fallback_reason"]
                if candidate
                else "WRITER_ERROR"
            ),
            "error": candidate["error"] if candidate else evidence_error,
        },
        "query_plan": (
            candidate["query_plan"].model_dump()
            if candidate
            else None
        ),
        "retrieval": {
            "complete": (
                candidate["retrieval"].completeness.retrieval_complete
                if candidate
                else False
            ),
            "missing_evidence": (
                candidate["retrieval"].completeness.missing_evidence
                if candidate
                else []
            ),
            "strategies": (
                candidate["retrieval"].strategies_used
                if candidate
                else []
            ),
        },
        "evidence_bundle": (
            limit_evidence_bundle(
                candidate["evidence_bundle"].model_dump(),
                limit=request.evidence_limit,
                full=request.full,
            )
            if candidate
            else None
        ),
    }
