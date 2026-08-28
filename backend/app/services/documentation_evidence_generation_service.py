import json
import logging
import os
import re
import time
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

from app.guardrails.models import GuardrailResult, GuardrailStatus
from app.guardrails.output_guard import OutputGuardrail
from app.guardrails.retrieval_guard import RetrievalEvidenceGuardrail
from app.models.document_content import (
    ContentUnit,
    EvidenceBundle,
    EvidenceItem,
    EvidenceTable,
)
from app.models.conversation_memory import ResolvedConversationQuery
from app.services.adaptive_retrieval_service import (
    AdaptiveRetrievalResult,
    AdaptiveRetriever,
)
from app.services.evidence_builder_service import EvidenceBuilder
from app.services.generic_question_classifier_service import (
    ClassificationResult,
    GenericQuestionClassifier,
    QueryPlan,
    QueryPlanner,
)
from app.services.entity_consistency_service import (
    check_evidence_completeness,
    filter_units_for_entity_consistency,
)
from app.services.hps_ai_service import call_hps_ai
from app.services.conversation_memory_service import resolve_documentation_query
from app.services.field_value_decoder_service import (
    decode_field_value_from_evidence,
    field_value_decoding_table_block,
)


logger = logging.getLogger(__name__)

ALLOWED_EVIDENCE_INTENTS = {
    "VALUE_LOOKUP",
    "TABLE_LOOKUP",
    "FIELD_LOOKUP",
    "DEFINITION",
}


class EvidencePipelineFallback(RuntimeError):
    """Raised when the experimental Evidence pipeline must fall back."""


class AnswerRequirements(BaseModel):
    definition: bool = False
    role: bool = False
    structure: bool = False
    usage: bool = False
    trace_usage: bool = False
    values: bool = False
    table: bool = False
    all_values: bool = False
    example: bool = False
    citations: bool = True


class EvidenceValidationResult(BaseModel):
    valid: bool = True
    missing_requirements: list[str] = Field(default_factory=list)
    fact_mismatches: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)


class EvidenceLatencyBreakdown(BaseModel):
    classification_ms: int = 0
    adaptive_retrieval_ms: int = 0
    evidence_builder_ms: int = 0
    writer_ms: int = 0
    validator_ms: int = 0
    total_evidence_ms: int = 0


def env_flag(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}


def evidence_generation_enabled() -> bool:
    return env_flag("DOCUMENTATION_EVIDENCE_GENERATION")


def evidence_table_lookup_generation_enabled() -> bool:
    value = os.getenv("DOCUMENTATION_EVIDENCE_TABLE_LOOKUP", "true")
    return value.lower() in {"1", "true", "yes", "on"}


def evidence_shadow_enabled() -> bool:
    value = os.getenv("DOCUMENTATION_EVIDENCE_SHADOW", "true")
    return value.lower() in {"1", "true", "yes", "on"}


def evidence_pipeline_allowed(intent: str) -> bool:
    return intent in ALLOWED_EVIDENCE_INTENTS


def normalize_text(value: Any) -> str:
    text = " ".join(str(value or "").lower().split())
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )


def extract_answer_requirements(
    question: str,
    classification: ClassificationResult,
) -> AnswerRequirements:
    text = normalize_text(question)
    intent = classification.intent
    requirements = AnswerRequirements()

    if intent == "VALUE_LOOKUP":
        requirements.values = True
    elif intent == "TABLE_LOOKUP":
        requirements.values = True
        requirements.table = True
    elif intent == "FIELD_LOOKUP":
        requirements.definition = True
        requirements.role = True
        requirements.usage = True
    elif intent == "DEFINITION":
        requirements.definition = True
        requirements.role = True

    if re.search(r"\b(role|rôle|represente|représente|utilite|utilité)\b", text):
        requirements.role = True

    if re.search(r"\b(structure|format|longueur|length|type|attribut)\b", text):
        requirements.structure = True

    if re.search(r"\b(usage|utilisation|fonctionnement|sert|sert-il)\b", text):
        requirements.usage = True

    if re.search(r"\b(trace|log|analyse|transaction)\b", text):
        requirements.trace_usage = True

    if re.search(r"\b(code|codes|valeur|valeurs|signification|tableau|table)\b", text):
        requirements.values = True
        requirements.table = True

    if re.search(r"\b(exemple|example|concret|interpretation|interprétation)\b", text):
        requirements.example = True

    if re.search(
        r"\b(tous|toutes|complete|completee|liste complete|all|every)\b",
        text,
    ):
        requirements.all_values = True
        requirements.table = True
        requirements.values = True

    if re.search(r"\b(cite|citer|citation|source|sources|reference|référence|page)\b", text):
        requirements.citations = True

    return requirements


def section_document_ids(sections: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys([
        str(section.get("document_id"))
        for section in sections
        if section.get("document_id")
    ]))


def compact_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").split())

    if len(text) <= limit:
        return text

    return f"{text[:limit].rstrip()}..."


def unit_by_id(bundle: EvidenceBundle) -> dict[str, ContentUnit]:
    return {
        unit.unit_id: unit
        for unit in bundle.retrieved_units
    }


def all_bundle_items(bundle: EvidenceBundle) -> list[EvidenceItem]:
    return [
        *bundle.definitions,
        *bundle.facts,
        *bundle.rules,
        *bundle.examples,
        *bundle.process_steps,
        *bundle.other_evidence,
        *bundle.citations,
    ]


def bundle_has_evidence(bundle: EvidenceBundle) -> bool:
    return bool(all_bundle_items(bundle) or bundle.tables)


def evidence_id_map(bundle: EvidenceBundle) -> dict[str, str]:
    mapping = {}

    for index, item in enumerate(all_bundle_items(bundle), start=1):
        if item.source_unit_id and item.source_unit_id not in mapping:
            mapping[item.source_unit_id] = f"E{index}"

    return mapping


def source_ids(items: list[EvidenceItem]) -> list[str]:
    return list(dict.fromkeys([
        item.source_unit_id
        for item in items
        if item.source_unit_id
    ]))


def reference_from_item(
    item: EvidenceItem,
    units: dict[str, ContentUnit],
) -> dict[str, Any]:
    unit = units.get(item.source_unit_id or "")
    source = unit.source if unit else "Document"

    return {
        "source": source or "Document",
        "pdf_page": item.pdf_page,
        "printed_page": item.printed_page,
        "source_id": item.source_unit_id,
        "section": item.section,
    }


def unique_references(
    references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen = set()
    unique = []

    for reference in references:
        key = (
            reference.get("source"),
            reference.get("pdf_page"),
            reference.get("printed_page"),
            reference.get("source_id"),
            reference.get("section"),
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(reference)

    return unique


def references_from_items(
    items: list[EvidenceItem],
    bundle: EvidenceBundle,
) -> list[dict[str, Any]]:
    units = unit_by_id(bundle)

    return unique_references([
        reference_from_item(item, units)
        for item in items
    ])


def evidence_items_for_unit_ids(
    bundle: EvidenceBundle,
    unit_ids: list[str],
) -> list[EvidenceItem]:
    wanted = {
        str(unit_id)
        for unit_id in unit_ids
        if unit_id
    }
    found = [
        item
        for item in all_bundle_items(bundle)
        if item.source_unit_id in wanted
    ]
    found_ids = {
        item.source_unit_id
        for item in found
        if item.source_unit_id
    }
    units = unit_by_id(bundle)

    for unit_id in wanted - found_ids:
        unit = units.get(unit_id)

        if not unit:
            continue

        found.append(
            EvidenceItem(
                content=unit.content,
                source_unit_id=unit.unit_id,
                document_id=unit.document_id,
                pdf_page=unit.source_location.pdf_page,
                printed_page=unit.source_location.printed_page,
                section=unit.hierarchy.title,
                content_type=unit.content_type,
                entity_alignment=unit.entity_alignment,
                confidence=unit.parsing_confidence,
            )
        )

    return found


def reference_label(reference: dict[str, Any]) -> str:
    parts = []

    if reference.get("pdf_page"):
        parts.append(f"PDF p.{reference['pdf_page']}")

    if reference.get("printed_page"):
        parts.append(f"printed {reference['printed_page']}")

    return " / ".join(parts)


def split_mapping_text(
    item: EvidenceItem,
    unit: ContentUnit | None,
) -> tuple[str, str]:
    code = ""

    if unit:
        code = str((unit.entities or {}).get("code") or "").strip()

    text = " ".join(str(item.content or "").split())
    meaning = text

    for separator in (" = ", ": ", " | "):
        if separator in text:
            left, right = text.split(separator, 1)
            if not code:
                code = left.strip()
            meaning = right.strip()
            break

    if code and meaning.startswith(code):
        meaning = meaning[len(code):].lstrip(" :=|-")

    return code, meaning


def code_mapping_rows(
    bundle: EvidenceBundle,
) -> tuple[list[dict[str, str]], list[EvidenceItem]]:
    units = unit_by_id(bundle)
    evidence_ids = evidence_id_map(bundle)
    references = {
        reference.get("source_id"): reference
        for reference in references_from_items(all_bundle_items(bundle), bundle)
    }
    rows = []
    items = []
    seen_codes = set()

    for item in bundle.facts:
        if item.content_type != "code_mapping":
            continue

        unit = units.get(item.source_unit_id or "")
        code, meaning = split_mapping_text(item, unit)

        if not code or not meaning or code in seen_codes:
            continue

        seen_codes.add(code)
        items.append(item)
        rows.append({
            "code": code,
            "meaning": meaning,
            "reference": reference_label(
                references.get(item.source_unit_id) or {}
            ),
            "evidence_id": evidence_ids.get(item.source_unit_id or "", ""),
        })

    return rows, items


def table_rows_for_response(
    table: EvidenceTable,
    bundle: EvidenceBundle,
) -> tuple[list[dict[str, str]], list[EvidenceItem]]:
    evidence_ids = evidence_id_map(bundle)
    items_by_unit = {
        item.source_unit_id: item
        for item in all_bundle_items(bundle)
        if item.source_unit_id
    }
    references = {
        reference.get("source_id"): reference
        for reference in references_from_items(all_bundle_items(bundle), bundle)
    }
    rows = []
    items = []
    seen = set()

    for row in table.rows:
        code = str(row.get("col_1") or row.get("code") or "").strip()
        meaning = str(row.get("col_2") or row.get("meaning") or "").strip()
        source_unit_id = row.get("source_unit_id")

        if not code or not meaning:
            continue

        key = (code, meaning)
        if key in seen:
            continue

        seen.add(key)
        item = items_by_unit.get(source_unit_id)

        if item:
            items.append(item)

        rows.append({
            "code": code,
            "meaning": meaning,
            "reference": reference_label(
                references.get(source_unit_id) or {}
            ),
            "evidence_id": evidence_ids.get(str(source_unit_id or ""), ""),
        })

    return rows, items


def table_column_label(key: str, index: int) -> str:
    normalized = normalize_text(key)

    if normalized in {"code", "col_1", "value", "valeur"}:
        return "Code"

    if normalized in {"meaning", "description", "definition", "col_2"}:
        return "Signification"

    if normalized in {"reference", "source"}:
        return "Reference"

    if key.startswith("col_"):
        return f"Colonne {index + 1}"

    return str(key).replace("_", " ").title()


def structured_table_for_response(
    table: EvidenceTable,
    bundle: EvidenceBundle,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[EvidenceItem]]:
    evidence_ids = evidence_id_map(bundle)
    items_by_unit = {
        item.source_unit_id: item
        for item in all_bundle_items(bundle)
        if item.source_unit_id
    }
    references = {
        reference.get("source_id"): reference
        for reference in references_from_items(all_bundle_items(bundle), bundle)
    }
    columns = list(table.columns or [])

    if not columns:
        max_columns = max(
            (
                len([
                    key
                    for key in row
                    if key not in {"source_unit_id", "evidence_id", "reference"}
                ])
                for row in table.rows
            ),
            default=0,
        )
        columns = [f"col_{index + 1}" for index in range(max_columns)]

    use_code_meaning_aliases = (
        columns == ["col_1", "col_2"]
        and all(
            str(row.get("col_1") or "").strip()
            and str(row.get("col_2") or "").strip()
            for row in table.rows
        )
    )

    output_columns = ["code", "meaning"] if use_code_meaning_aliases else columns

    response_columns = [
        {
            "key": column,
            "label": table_column_label(column, index),
        }
        for index, column in enumerate(output_columns)
    ]

    if response_columns:
        response_columns.append({"key": "reference", "label": "Reference"})

    rows = []
    items = []
    seen = set()

    for row in table.rows:
        if use_code_meaning_aliases:
            response_row = {
                "code": str(row.get("col_1") or "").strip(),
                "meaning": str(row.get("col_2") or "").strip(),
            }
        else:
            response_row = {
                column: str(row.get(column) or "").strip()
                for column in columns
            }
        source_unit_id = str(row.get("source_unit_id") or "")
        reference = references.get(source_unit_id) or {}
        response_row["reference"] = reference_label(reference)
        response_row["evidence_id"] = evidence_ids.get(source_unit_id, "")

        identity = tuple(response_row.get(column, "") for column in output_columns)

        if not any(identity) or identity in seen:
            continue

        seen.add(identity)
        item = items_by_unit.get(source_unit_id)

        if item:
            items.append(item)

        rows.append(response_row)

    return response_columns, rows, items


def select_example_row(
    rows: list[dict[str, str]],
) -> dict[str, str] | None:
    for row in rows:
        meaning = normalize_text(row.get("meaning"))
        if not meaning:
            continue

        if any(
            term in meaning
            for term in (
                "declin",
                "refus",
                "insufficient",
                "incorrect",
                "error",
                "not allowed",
                "invalid",
            )
        ):
            return row

    return rows[0] if rows else None


def requested_code_rows(
    bundle: EvidenceBundle,
    codes: list[str],
) -> tuple[list[dict[str, str]], list[EvidenceItem], list[str]]:
    rows_by_code: dict[str, tuple[dict[str, str], EvidenceItem | None]] = {}

    for row, item in zip(*code_mapping_rows(bundle)):
        code = str(row.get("code") or "").strip().upper()

        if code and code not in rows_by_code:
            rows_by_code[code] = (row, item)

    for table in bundle.tables:
        table_rows, table_items = table_rows_for_response(table, bundle)
        item_by_evidence_id = {
            evidence_id_map(bundle).get(item.source_unit_id or ""): item
            for item in table_items
        }

        for row in table_rows:
            code = str(row.get("code") or row.get("col_1") or "").strip().upper()

            if not code or code in rows_by_code:
                continue

            if "code" not in row and row.get("col_1"):
                row = {
                    **row,
                    "code": str(row.get("col_1") or "").strip(),
                    "meaning": str(row.get("col_2") or "").strip(),
                }

            rows_by_code[code] = (
                row,
                item_by_evidence_id.get(row.get("evidence_id")),
            )

    selected_rows: list[dict[str, str]] = []
    selected_items: list[EvidenceItem] = []
    missing_codes: list[str] = []

    for code in codes:
        normalized_code = str(code or "").strip().upper()
        match = rows_by_code.get(normalized_code)

        if not match:
            missing_codes.append(normalized_code)
            continue

        row, item = match
        selected_rows.append(row)

        if item:
            selected_items.append(item)

    return selected_rows, selected_items, missing_codes


def field_context_items(
    bundle: EvidenceBundle,
    requirements: AnswerRequirements,
) -> list[EvidenceItem]:
    items = []

    if requirements.role or requirements.definition:
        items.extend(bundle.definitions[:2])

    if requirements.structure or requirements.usage or requirements.trace_usage:
        items.extend([
            item
            for item in bundle.facts
            if item.content_type in {"field_attribute", "field_usage"}
        ][:6])

    return list({
        item.source_unit_id or f"{index}-{item.content}": item
        for index, item in enumerate(items)
    }.values())


def build_requested_codes_response(
    question: str,
    classification: ClassificationResult,
    bundle: EvidenceBundle,
    requirements: AnswerRequirements | None = None,
) -> dict[str, Any] | None:
    requirements = requirements or extract_answer_requirements(
        question,
        classification,
    )
    field = (
        classification.entities.field_numbers[0]
        if classification.entities.field_numbers
        else None
    )
    requested_codes = [
        str(code).upper()
        for code in classification.entities.codes
        if code
    ]

    if not field or len(requested_codes) < 2:
        return None

    rows, row_items, missing_codes = requested_code_rows(
        bundle,
        requested_codes,
    )
    context_items = field_context_items(bundle, requirements)

    if not rows:
        return build_insufficient_evidence_response(
            (
                f"Aucun mapping fiable n'a ete retrouve pour les codes "
                f"demandes du Field {field}: {', '.join(requested_codes)}."
            ),
            bundle,
        )

    all_items = [*context_items, *row_items]
    sections: list[dict[str, Any]] = []
    context_content = " ".join(
        compact_text(item.content, 500)
        for item in context_items
        if item.content
    )

    if context_content:
        sections.append({
            "title": "Role, structure et usage",
            "content": context_content,
            "source_ids": source_ids(context_items),
        })

    sections.append({
        "title": "Codes demandes",
        "content": (
            f"Voici uniquement les codes explicitement demandes pour le "
            f"Field {field}."
        ),
        "blocks": [
            {
                "type": "table",
                "title": f"Field {field} - codes demandes",
                "columns": [
                    {"key": "code", "label": "Code"},
                    {"key": "meaning", "label": "Signification"},
                    {"key": "reference", "label": "Reference"},
                ],
                "rows": rows,
            }
        ],
        "source_ids": source_ids(row_items),
    })

    if requirements.example:
        example_row = (
            next((row for row in rows if row.get("code") == "51"), None)
            or select_example_row(rows)
        )

        if example_row:
            sections.append({
                "title": "Exemple",
                "content": (
                    f"Si une trace contient FLD ({field}) = "
                    f"{example_row['code']}, l'interpretation documentee est "
                    f"\"{example_row['meaning']}\"."
                ),
                "source_ids": source_ids(row_items),
            })

    issues = []

    if missing_codes:
        issues.append({
            "severity": "warning",
            "title": "Mappings manquants",
            "detail": (
                "Les codes suivants ont ete demandes mais n'ont pas ete "
                "retrouves dans les mappings structures disponibles: "
                + ", ".join(missing_codes)
            ),
        })

    return {
        "summary": (
            f"Les mappings demandes pour le Field {field} ont ete extraits "
            "depuis les donnees structurees disponibles. Les libelles "
            "documentaires originaux sont conserves."
        ),
        "sections": sections,
        "story": [],
        "issues": issues,
        "recommendations": [],
        "references": references_from_items(all_items, bundle),
    }


def first_code_mapping(
    bundle: EvidenceBundle,
    code: str | None,
) -> EvidenceItem | None:
    units = unit_by_id(bundle)

    for item in bundle.facts:
        if item.content_type != "code_mapping":
            continue

        unit = units.get(item.source_unit_id or "")
        unit_code, _ = split_mapping_text(item, unit)

        if not code or unit_code == code:
            return item

    for item in bundle.facts:
        if item.content_type != "table_row" or not code:
            continue

        text = item.content.strip()

        if text.startswith(f"{code} ") or text.startswith(f"{code} |"):
            return item

    return None


def build_table_lookup_response(
    question: str,
    bundle: EvidenceBundle,
    requirements: AnswerRequirements | None = None,
) -> dict[str, Any] | None:
    rows, row_items = code_mapping_rows(bundle)
    table_title = "Valeurs documentaires"
    table_columns = [
        {"key": "code", "label": "Code"},
        {"key": "meaning", "label": "Signification"},
        {"key": "reference", "label": "Reference"},
    ]
    table_source_ids: list[str] = source_ids(row_items)

    if not rows and bundle.tables:
        table = max(bundle.tables, key=lambda item: len(item.rows))
        table_columns, rows, row_items = structured_table_for_response(
            table,
            bundle,
        )
        table_title = table.title or table_title
        table_source_ids = list(dict.fromkeys([
            *table.source_unit_ids,
            *source_ids(row_items),
        ]))

    if not rows:
        return None

    sections = [
        {
            "title": "Valeurs et significations",
            "content": "",
            "blocks": [
                {
                    "type": "table",
                    "title": table_title,
                    "columns": table_columns,
                    "rows": rows,
                }
            ],
            "source_ids": table_source_ids,
        }
    ]

    if requirements and requirements.example:
        example_row = select_example_row(rows)

        if example_row:
            sections.append({
                "title": "Exemple",
                "content": (
                    f"Si une trace contient ce champ avec le code "
                    f"{example_row['code']}, l'interpretation documentee "
                    f"est \"{example_row['meaning']}\"."
                ),
                "source_ids": table_source_ids,
            })

    return {
        "summary": (
            "Les valeurs demandees ont ete retrouvees dans les donnees "
            "structurees extraites de la documentation. Le tableau reprend "
            "uniquement les codes presents dans les sources."
        ),
        "sections": sections,
        "story": [],
        "issues": [],
        "recommendations": [],
        "references": references_from_items(row_items, bundle),
    }


def build_value_lookup_response(
    question: str,
    classification: ClassificationResult,
    bundle: EvidenceBundle,
    requirements: AnswerRequirements | None = None,
) -> dict[str, Any] | None:
    code = classification.entities.codes[0] if classification.entities.codes else None
    field = (
        classification.entities.field_numbers[0]
        if classification.entities.field_numbers
        else None
    )
    decoding = (
        decode_field_value_from_evidence(
            bundle=bundle,
            field_number=field,
            value=code,
        )
        if field and code
        else None
    )

    if decoding and len(decoding.rows) >= 2:
        decoding_items = evidence_items_for_unit_ids(bundle, decoding.source_ids)
        sections = [
            {
                "title": "Valeur observee",
                "content": f"Field {decoding.field_number} = {decoding.raw_value}",
                "source_ids": source_ids(decoding_items),
            },
            {
                "title": "Decodage par positions",
                "content": (
                    "La valeur est composee de sous-champs documentes. "
                    "Le backend la decoupe selon les positions retrouvees "
                    "dans l'EvidenceBundle."
                ),
                "blocks": [field_value_decoding_table_block(decoding)],
                "source_ids": source_ids(decoding_items),
            },
        ]
        issues = [
            {
                "severity": "warning",
                "title": "Decodage partiel",
                "detail": issue,
            }
            for issue in decoding.issues
        ]

        return {
            "summary": (
                f"Le Field {decoding.field_number} vaut {decoding.raw_value}. "
                "Son interpretation doit etre lue par sous-positions."
            ),
            "sections": sections,
            "story": [],
            "issues": issues,
            "recommendations": [],
            "references": references_from_items(decoding_items, bundle),
        }

    mapping = first_code_mapping(bundle, code)

    if not mapping:
        return None

    unit = unit_by_id(bundle).get(mapping.source_unit_id or "")
    _, meaning = split_mapping_text(mapping, unit)

    if not meaning:
        return None

    summary = (
        f"Le code {code} du Field {field} correspond a \"{meaning}\"."
        if code and field
        else f"La valeur demandee correspond a \"{meaning}\"."
    )

    sections = [
        {
            "title": "Analyse",
            "content": (
                "Cette signification provient du mapping structure extrait "
                "de la documentation. Elle indique le sens fonctionnel du "
                "code dans le champ concerne, sans ajouter de valeurs non "
                "presentes dans les sources."
            ),
            "source_ids": source_ids([mapping]),
        }
    ]

    if requirements and requirements.example and code and field:
        sections.append({
            "title": "Exemple",
            "content": (
                f"Si une trace contient FLD ({field}) = {code}, "
                f"l'interpretation documentee du code est \"{meaning}\"."
            ),
            "source_ids": source_ids([mapping]),
        })

    return {
        "summary": summary,
        "sections": sections,
        "story": [],
        "issues": [],
        "recommendations": [],
        "references": references_from_items([mapping], bundle),
    }


def build_insufficient_evidence_response(
    reason: str,
    bundle: EvidenceBundle,
) -> dict[str, Any]:
    return {
        "summary": (
            "Les sources structurees recuperees ne suffisent pas pour produire "
            "une reponse fiable avec le pipeline Evidence."
        ),
        "sections": [],
        "story": [],
        "issues": [
            {
                "severity": "warning",
                "title": "Evidence insuffisante",
                "detail": reason,
            }
        ],
        "recommendations": [],
        "references": references_from_items(bundle.citations, bundle),
    }


def evidence_context_for_llm(
    bundle: EvidenceBundle,
) -> str:
    units = unit_by_id(bundle)
    evidence_rows = []

    for index, item in enumerate(all_bundle_items(bundle), start=1):
        unit = units.get(item.source_unit_id or "")
        evidence_rows.append({
            "evidence_id": f"E{index}",
            "content": compact_text(item.content, 650),
            "content_type": item.content_type,
            "source_unit_id": item.source_unit_id,
            "source": unit.source if unit else None,
            "section": item.section,
            "pdf_page": item.pdf_page,
            "printed_page": item.printed_page,
        })

    tables = [
        {
            "title": table.title,
            "columns": table.columns,
            "rows": table.rows[:120],
            "source_unit_ids": table.source_unit_ids,
        }
        for table in bundle.tables
    ]

    return json.dumps(
        {
            "evidence": evidence_rows[:80],
            "tables": tables,
        },
        ensure_ascii=False,
    )


def parse_json_response(content: str) -> dict[str, Any]:
    cleaned = content.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]

    payload = json.loads(cleaned)

    if not isinstance(payload, dict):
        raise ValueError("EvidenceResponseWriter returned non-object JSON")

    return unwrap_nested_writer_payload(payload)


def unwrap_nested_writer_payload(
    payload: dict[str, Any],
    max_depth: int = 3,
) -> dict[str, Any]:
    current = payload

    for _ in range(max_depth):
        summary = current.get("summary")

        if isinstance(summary, dict):
            nested = summary
        elif isinstance(summary, str) and summary.strip().startswith("{"):
            try:
                nested = json.loads(summary)
            except json.JSONDecodeError:
                break
        else:
            break

        if not isinstance(nested, dict) or not (
            "summary" in nested
            or "sections" in nested
            or "references" in nested
        ):
            break

        metadata = {
            key: value
            for key, value in current.items()
            if str(key).startswith("_")
        }
        current = {
            **current,
            **nested,
            **metadata,
        }

    return current


def normalize_writer_payload(
    payload: dict[str, Any],
    bundle: EvidenceBundle,
) -> dict[str, Any]:
    payload = unwrap_nested_writer_payload(payload)
    references = references_from_items(all_bundle_items(bundle), bundle)
    valid_source_ids = {
        reference.get("source_id")
        for reference in references
        if reference.get("source_id")
    }
    sections = []

    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue

        source_list = [
            str(source_id)
            for source_id in section.get("source_ids") or []
            if str(source_id) in valid_source_ids
        ]
        sections.append({
            "title": str(section.get("title") or "Analyse"),
            "content": str(section.get("content") or ""),
            "blocks": section.get("blocks") or [],
            "source_ids": source_list,
        })

    return {
        "summary": str(payload.get("summary") or "").strip(),
        "sections": sections,
        "story": [],
        "issues": payload.get("issues") or [],
        "recommendations": payload.get("recommendations") or [],
        "references": references,
    }


def response_text(response: dict[str, Any]) -> str:
    chunks = [str(response.get("summary") or "")]

    for section in response.get("sections") or []:
        if not isinstance(section, dict):
            continue

        chunks.append(str(section.get("title") or ""))
        chunks.append(str(section.get("content") or ""))

        for block in section.get("blocks") or []:
            chunks.append(json.dumps(block, ensure_ascii=False, default=str))

    return normalize_text(" ".join(chunks))


def requirement_present(
    response: dict[str, Any],
    names: tuple[str, ...],
) -> bool:
    text = response_text(response)

    return any(name in text for name in names)


def response_table_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []

    for section in response.get("sections") or []:
        if not isinstance(section, dict):
            continue

        for block in section.get("blocks") or []:
            if not isinstance(block, dict) or block.get("type") != "table":
                continue

            for row in block.get("rows") or []:
                if isinstance(row, dict):
                    rows.append(row)

    return rows


def evidence_mappings(bundle: EvidenceBundle) -> dict[str, str]:
    mappings = {}
    units = unit_by_id(bundle)

    for item in bundle.facts:
        if item.content_type not in {"code_mapping", "table_row"}:
            continue

        unit = units.get(item.source_unit_id or "")
        code, meaning = split_mapping_text(item, unit)

        if code and meaning and code not in mappings:
            mappings[code] = meaning

    for table in bundle.tables:
        for row in table.rows:
            code = str(row.get("col_1") or row.get("code") or "").strip()
            meaning = str(row.get("col_2") or row.get("meaning") or "").strip()

            if code and meaning and code not in mappings:
                mappings[code] = meaning

    return mappings


def validate_evidence_response(
    *,
    response: dict[str, Any] | None,
    requirements: AnswerRequirements,
    bundle: EvidenceBundle,
) -> EvidenceValidationResult:
    result = EvidenceValidationResult()

    if not response:
        result.missing_requirements.append("response")
        result.valid = False
        return result

    text = response_text(response)

    if requirements.definition and not response.get("summary"):
        result.missing_requirements.append("definition")

    if requirements.role and not requirement_present(
        response,
        ("role", "rôle", "represente", "représente", "sert"),
    ):
        result.missing_requirements.append("role")

    if requirements.structure and not requirement_present(
        response,
        ("structure", "format", "longueur", "length", "type", "attribut"),
    ):
        result.missing_requirements.append("structure")

    if requirements.usage and not requirement_present(
        response,
        ("usage", "utilisation", "utilise", "utilisé", "sert"),
    ):
        result.missing_requirements.append("usage")

    if requirements.trace_usage and not requirement_present(
        response,
        ("trace", "transaction", "log", "analyse"),
    ):
        result.missing_requirements.append("trace_usage")

    if requirements.values and not (
        response_table_rows(response)
        or requirement_present(response, ("code", "valeur", "signification"))
    ):
        result.missing_requirements.append("values")

    if requirements.table and not response_table_rows(response):
        result.missing_requirements.append("table")

    if requirements.all_values:
        available_rows = sum(len(table.rows) for table in bundle.tables)
        available_mappings = len(evidence_mappings(bundle))

        if not response_table_rows(response):
            result.missing_requirements.append("all_values")
        elif available_rows and len(response_table_rows(response)) < available_rows:
            result.missing_requirements.append("all_values")
        elif available_mappings and len(response_table_rows(response)) < available_mappings:
            result.missing_requirements.append("all_values")

    if requirements.example and not requirement_present(
        response,
        ("exemple", "example", "fld", "trace contient"),
    ):
        result.missing_requirements.append("example")

    if requirements.citations:
        has_references = bool(response.get("references"))
        has_section_sources = any(
            section.get("source_ids")
            for section in response.get("sections") or []
            if isinstance(section, dict)
        )
        has_row_evidence = any(
            row.get("evidence_id")
            for row in response_table_rows(response)
        )

        if not (has_references and (has_section_sources or has_row_evidence)):
            result.missing_requirements.append("citations")

    mappings = evidence_mappings(bundle)

    for row in response_table_rows(response):
        code = str(row.get("code") or "").strip()
        meaning = str(row.get("meaning") or "").strip()

        if not code:
            continue

        expected = mappings.get(code)

        if not expected:
            result.unsupported_claims.append(f"code {code}")
            continue

        if meaning and normalize_text(meaning) != normalize_text(expected):
            result.fact_mismatches.append({
                "code": code,
                "expected": expected,
                "observed": meaning,
            })

    valid_source_ids = {
        item.source_unit_id
        for item in all_bundle_items(bundle)
        if item.source_unit_id
    }
    valid_source_ids.update(
        source_id
        for table in bundle.tables
        for source_id in table.source_unit_ids
        if source_id
    )

    for section in response.get("sections") or []:
        if not isinstance(section, dict):
            continue

        for source_id in section.get("source_ids") or []:
            if source_id not in valid_source_ids:
                result.unsupported_claims.append(f"source_id {source_id}")

    result.valid = not (
        result.missing_requirements
        or result.fact_mismatches
        or result.unsupported_claims
    )

    return result


def evidence_candidate_diagnostics(
    *,
    retrieval: AdaptiveRetrievalResult,
    bundle: EvidenceBundle,
    response: dict[str, Any] | None,
) -> dict[str, Any]:
    content_units = retrieval.content_units or []
    content_type = lambda unit: (
        unit.get("content_type")
        if isinstance(unit, dict)
        else getattr(unit, "content_type", None)
    )
    table_units = [
        unit
        for unit in content_units
        if content_type(unit) == "table"
    ]
    table_row_units = [
        unit
        for unit in content_units
        if content_type(unit) == "table_row"
    ]
    code_mapping_units = [
        unit
        for unit in content_units
        if content_type(unit) == "code_mapping"
    ]
    evidence_table_rows = sum(len(table.rows) for table in bundle.tables)

    return {
        "retrieved_units": len(content_units),
        "retrieved_tables": len(table_units),
        "retrieved_table_rows": len(table_row_units),
        "retrieved_code_mappings": len(code_mapping_units),
        "evidence_tables": len(bundle.tables),
        "evidence_table_rows": evidence_table_rows,
        "evidence_code_mappings": len(evidence_mappings(bundle)),
        "response_table_rows": len(response_table_rows(response or {})),
    }


class EvidenceResponseWriter:
    """Genere une reponse experimentale depuis EvidenceBundle uniquement."""

    @staticmethod
    async def generate(
        *,
        question: str,
        classification: ClassificationResult,
        query_plan: QueryPlan,
        bundle: EvidenceBundle,
        requirements: AnswerRequirements | None = None,
        validation_feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requirements = requirements or extract_answer_requirements(
            question,
            classification,
        )

        deterministic = build_requested_codes_response(
            question,
            classification,
            bundle,
            requirements,
        )

        if deterministic:
            return deterministic

        if classification.intent == "TABLE_LOOKUP":
            deterministic = build_table_lookup_response(
                question,
                bundle,
                requirements,
            )

            if deterministic:
                return deterministic

        if classification.intent == "VALUE_LOOKUP":
            deterministic = build_value_lookup_response(
                question,
                classification,
                bundle,
                requirements,
            )

            if deterministic:
                return deterministic

        context = evidence_context_for_llm(bundle)

        if not context or context == '{"evidence": [], "tables": []}':
            return build_insufficient_evidence_response(
                "EvidenceBundle vide.",
                bundle,
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "Tu es un consultant technique senior Visa/HPS. Tu dois "
                    "rediger une reponse uniquement a partir de l'EvidenceBundle "
                    "fourni. N'invente jamais une valeur, une definition, un code "
                    "ou une regle absente de l'evidence. Reformule les faits en "
                    "francais technique naturel. Ne copie pas les extraits. "
                    "Retourne uniquement un JSON valide avec: summary, sections. "
                    "Chaque section doit contenir title, content, source_ids. "
                    "Les source_ids doivent etre des source_unit_id presents dans "
                    "l'evidence. Respecte les AnswerRequirements. Si une "
                    "requirement ne peut pas etre satisfaite par l'evidence, "
                    "dis-le clairement sans inventer."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question:\n{question}\n\n"
                    f"Intent:\n{classification.intent}\n\n"
                    f"Entities:\n{classification.entities.model_dump_json()}\n\n"
                    f"QueryPlan:\n{query_plan.model_dump_json()}\n\n"
                    f"AnswerRequirements:\n{requirements.model_dump_json()}\n\n"
                    f"ValidationFeedback:\n"
                    f"{json.dumps(validation_feedback or {}, ensure_ascii=False)}\n\n"
                    f"EvidenceBundle:\n{context}"
                ),
            },
        ]
        content = await call_hps_ai(
            messages,
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )
        payload = parse_json_response(content)
        normalized = normalize_writer_payload(payload, bundle)

        if not normalized.get("summary"):
            raise ValueError("EvidenceResponseWriter returned an empty summary")

        return normalized


async def build_evidence_bundle_for_sections(
    *,
    question: str,
    sections: list[dict[str, Any]],
    chunks_limit: int = 8,
    units_limit: int | None = None,
) -> tuple[
    ClassificationResult,
    QueryPlan,
    AdaptiveRetrievalResult,
    EvidenceBundle,
]:
    classification = GenericQuestionClassifier.classify(question)
    query_plan = QueryPlanner.build(classification)
    document_ids = section_document_ids(sections)
    retrieval = await AdaptiveRetriever.retrieve(
        question=question,
        sections=sections,
        document_ids=document_ids,
        plan=query_plan,
        sections_limit=chunks_limit,
        units_limit=units_limit,
    )
    filtered_units, filter_diagnostics = filter_units_for_entity_consistency(
        retrieval.content_units,
        query_plan,
    )
    retrieval.content_units = filtered_units
    rule_missing = check_evidence_completeness(
        plan=query_plan,
        units=filtered_units,
    )

    if rule_missing:
        retrieval.completeness.retrieval_complete = False
        retrieval.completeness.missing_evidence.extend(rule_missing)

    retrieval.expansions.append({
        "type": "entity_consistency_filter",
        **filter_diagnostics,
    })
    evidence_entities: dict[str, Any] = {}

    if classification.intent != "COMPARISON":
        if classification.entities.field_numbers:
            evidence_entities["field_number"] = classification.entities.field_numbers[0]

        if len(classification.entities.codes) == 1:
            evidence_entities["code"] = classification.entities.codes[0]

        if classification.entities.message_types:
            evidence_entities["message_type"] = classification.entities.message_types[0]

    bundle = EvidenceBundle.model_validate(
        EvidenceBuilder.build(
            question=question,
            intent=classification.intent,
            entities=evidence_entities,
            retrieved_sections=retrieval.sections,
            content_units=retrieval.content_units,
            limit=None if query_plan.require_complete_table else 80,
        )
    )

    return classification, query_plan, retrieval, bundle


async def build_evidence_candidate(
    *,
    question: str,
    sections: list[dict[str, Any]],
    chunks_limit: int = 8,
    units_limit: int | None = None,
    original_question: str | None = None,
    conversation_id: str | None = None,
    memory_resolution: ResolvedConversationQuery | None = None,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    latency = EvidenceLatencyBreakdown()
    effective_question = question

    if memory_resolution is None and conversation_id:
        try:
            memory_resolution, _memory_state, _recent_messages = await resolve_documentation_query(
                question=original_question or question,
                conversation_id=conversation_id,
            )
        except Exception as error:
            logger.info("MEMORY_RESOLUTION_ERROR %s", error)

    if memory_resolution:
        effective_question = memory_resolution.resolved_query

    classification_started = time.perf_counter()
    classification = GenericQuestionClassifier.classify(effective_question)
    query_plan = QueryPlanner.build(classification)
    requirements = extract_answer_requirements(effective_question, classification)
    latency.classification_ms = round(
        (time.perf_counter() - classification_started) * 1000
    )

    retrieval_started = time.perf_counter()
    document_ids = section_document_ids(sections)
    retrieval = await AdaptiveRetriever.retrieve(
        question=effective_question,
        sections=sections,
        document_ids=document_ids,
        plan=query_plan,
        sections_limit=chunks_limit,
        units_limit=units_limit,
    )
    filtered_units, filter_diagnostics = filter_units_for_entity_consistency(
        retrieval.content_units,
        query_plan,
    )
    retrieval.content_units = filtered_units
    rule_missing = check_evidence_completeness(
        plan=query_plan,
        units=filtered_units,
    )

    if rule_missing:
        retrieval.completeness.retrieval_complete = False
        retrieval.completeness.missing_evidence.extend(rule_missing)

    retrieval.expansions.append({
        "type": "entity_consistency_filter",
        **filter_diagnostics,
    })
    latency.adaptive_retrieval_ms = round(
        (time.perf_counter() - retrieval_started) * 1000
    )

    builder_started = time.perf_counter()
    evidence_entities: dict[str, Any] = {}

    if classification.intent != "COMPARISON":
        if classification.entities.field_numbers:
            evidence_entities["field_number"] = classification.entities.field_numbers[0]

        if len(classification.entities.codes) == 1:
            evidence_entities["code"] = classification.entities.codes[0]

        if classification.entities.message_types:
            evidence_entities["message_type"] = classification.entities.message_types[0]

    bundle = EvidenceBundle.model_validate(
        EvidenceBuilder.build(
            question=effective_question,
            intent=classification.intent,
            entities=evidence_entities,
            retrieved_sections=retrieval.sections,
            content_units=retrieval.content_units,
            limit=None if query_plan.require_complete_table else 80,
        )
    )
    latency.evidence_builder_ms = round(
        (time.perf_counter() - builder_started) * 1000
    )

    supported = evidence_pipeline_allowed(classification.intent)
    fallback_reason = None
    response = None
    blocked_response = None
    error = None
    validation = EvidenceValidationResult(valid=False)
    output_guardrail = GuardrailResult.pass_(
        code="OUTPUT_NOT_EVALUATED",
        reason="No response was produced yet.",
    )
    retrieval_guardrail = RetrievalEvidenceGuardrail.validate(
        question=effective_question,
        intent=classification.intent,
        bundle=bundle,
        retrieval_complete=retrieval.completeness.retrieval_complete,
        query_plan=query_plan,
    )

    if not supported:
        fallback_reason = "UNSUPPORTED_INTENT"
    elif not retrieval_guardrail.passed:
        fallback_reason = retrieval_guardrail.code
    else:
        writer_started = time.perf_counter()

        try:
            response = await EvidenceResponseWriter.generate(
                question=effective_question,
                classification=classification,
                query_plan=query_plan,
                bundle=bundle,
                requirements=requirements,
            )
        except Exception as writer_error:
            error = str(writer_error)
            fallback_reason = "WRITER_ERROR"

        latency.writer_ms = round(
            (time.perf_counter() - writer_started) * 1000
        )

        validator_started = time.perf_counter()
        validation = validate_evidence_response(
            response=response,
            requirements=requirements,
            bundle=bundle,
        )
        latency.validator_ms = round(
            (time.perf_counter() - validator_started) * 1000
        )

        if response and not validation.valid and not error:
            writer_started = time.perf_counter()

            try:
                response = await EvidenceResponseWriter.generate(
                    question=effective_question,
                    classification=classification,
                    query_plan=query_plan,
                    bundle=bundle,
                    requirements=requirements,
                    validation_feedback=validation.model_dump(),
                )
            except Exception as writer_error:
                error = str(writer_error)
                fallback_reason = "WRITER_ERROR"

            latency.writer_ms += round(
                (time.perf_counter() - writer_started) * 1000
            )
            validator_started = time.perf_counter()
            validation = validate_evidence_response(
                response=response,
                requirements=requirements,
                bundle=bundle,
            )
            latency.validator_ms += round(
                (time.perf_counter() - validator_started) * 1000
            )

        if response and not validation.valid and not fallback_reason:
            if validation.fact_mismatches:
                fallback_reason = "FACT_MISMATCH"
            elif validation.missing_requirements:
                fallback_reason = "MISSING_REQUIREMENT"
            else:
                fallback_reason = "UNSUPPORTED_CLAIM"

        output_guardrail = OutputGuardrail.validate(
            response=response,
            documentary_evidence_used=bundle_has_evidence(bundle),
            known_document_ids=set(document_ids),
            evidence_bundle=bundle,
        )

        if output_guardrail.status == GuardrailStatus.BLOCK:
            if not fallback_reason:
                fallback_reason = output_guardrail.code
            blocked_response = response
            response = None

    latency.total_evidence_ms = round(
        (time.perf_counter() - total_started) * 1000
    )

    candidate_valid = bool(
        supported
        and retrieval_guardrail.passed
        and response
        and validation.valid
        and output_guardrail.status != GuardrailStatus.BLOCK
        and not error
    )

    return {
        "question": original_question or question,
        "resolved_question": effective_question,
        "memory_resolution": memory_resolution,
        "classification": classification,
        "query_plan": query_plan,
        "answer_requirements": requirements,
        "retrieval": retrieval,
        "evidence_bundle": bundle,
        "supported": supported,
        "response": response,
        "blocked_response": blocked_response,
        "validation": validation,
        "guardrails": {
            "retrieval": retrieval_guardrail.model_dump(),
            "output": output_guardrail.model_dump(),
        },
        "latency": latency,
        "diagnostics": evidence_candidate_diagnostics(
            retrieval=retrieval,
            bundle=bundle,
            response=response,
        ),
        "error": error,
        "fallback_reason": None if candidate_valid else fallback_reason,
        "candidate_valid": candidate_valid,
    }


async def try_generate_evidence_response(
    *,
    question: str,
    sections: list[dict[str, Any]],
    original_question: str | None = None,
    conversation_id: str | None = None,
    memory_resolution: ResolvedConversationQuery | None = None,
) -> dict[str, Any]:
    candidate = await build_evidence_candidate(
        question=question,
        sections=sections,
        chunks_limit=8,
        units_limit=None,
        original_question=original_question,
        conversation_id=conversation_id,
        memory_resolution=memory_resolution,
    )

    if not candidate["supported"]:
        raise EvidencePipelineFallback(
            f"intent {candidate['classification'].intent} is not enabled"
        )

    retrieval = candidate["retrieval"]

    if not retrieval.completeness.retrieval_complete:
        raise EvidencePipelineFallback(
            "retrieval incomplete: "
            + ", ".join(retrieval.completeness.missing_evidence)
        )

    bundle = candidate["evidence_bundle"]

    if not bundle_has_evidence(bundle):
        raise EvidencePipelineFallback("EvidenceBundle is empty")

    if not candidate["candidate_valid"]:
        raise EvidencePipelineFallback(
            candidate["fallback_reason"] or candidate["error"] or "invalid candidate"
        )

    return candidate["response"]


async def try_generate_table_lookup_response(
    *,
    question: str,
    sections: list[dict[str, Any]],
    original_question: str | None = None,
    conversation_id: str | None = None,
    memory_resolution: ResolvedConversationQuery | None = None,
) -> dict[str, Any]:
    classification = GenericQuestionClassifier.classify(question)

    if classification.intent != "TABLE_LOOKUP":
        raise EvidencePipelineFallback(
            f"intent {classification.intent} is not TABLE_LOOKUP"
        )

    candidate = await build_evidence_candidate(
        question=question,
        sections=sections,
        chunks_limit=8,
        units_limit=None,
        original_question=original_question,
        conversation_id=conversation_id,
        memory_resolution=memory_resolution,
    )

    if candidate["classification"].intent != "TABLE_LOOKUP":
        raise EvidencePipelineFallback(
            f"intent {candidate['classification'].intent} is not TABLE_LOOKUP"
        )

    if not candidate["candidate_valid"]:
        raise EvidencePipelineFallback(
            candidate["fallback_reason"] or candidate["error"] or "invalid table candidate"
        )

    return candidate["response"]


async def try_generate_requested_codes_response(
    *,
    question: str,
    sections: list[dict[str, Any]],
    original_question: str | None = None,
    conversation_id: str | None = None,
    memory_resolution: ResolvedConversationQuery | None = None,
) -> dict[str, Any]:
    classification = GenericQuestionClassifier.classify(question)

    if (
        not classification.entities.field_numbers
        or len(classification.entities.codes) < 2
    ):
        raise EvidencePipelineFallback("question does not request explicit code list")

    candidate = await build_evidence_candidate(
        question=question,
        sections=sections,
        chunks_limit=8,
        units_limit=None,
        original_question=original_question,
        conversation_id=conversation_id,
        memory_resolution=memory_resolution,
    )
    response = candidate.get("response")

    if response:
        return response

    deterministic = build_requested_codes_response(
        candidate["resolved_question"],
        candidate["classification"],
        candidate["evidence_bundle"],
        candidate["answer_requirements"],
    )

    if deterministic:
        return deterministic

    return build_insufficient_evidence_response(
        (
            "Les codes demandes ont ete detectes dans la question, mais les "
            "mappings structures correspondants n'ont pas ete retrouves dans "
            "l'EvidenceBundle."
        ),
        candidate["evidence_bundle"],
    )


async def shadow_evidence_response(
    *,
    question: str,
    sections: list[dict[str, Any]],
    original_question: str | None = None,
    conversation_id: str | None = None,
    memory_resolution: ResolvedConversationQuery | None = None,
) -> dict[str, Any]:
    try:
        candidate = await build_evidence_candidate(
            question=question,
            sections=sections,
            chunks_limit=8,
            units_limit=None,
            original_question=original_question,
            conversation_id=conversation_id,
            memory_resolution=memory_resolution,
        )
        return {
            "question": candidate.get("question"),
            "resolved_question": candidate.get("resolved_question"),
            "memory_resolution": (
                candidate["memory_resolution"].model_dump()
                if candidate.get("memory_resolution")
                else None
            ),
            "intent": candidate["classification"].intent,
            "answer_requirements": candidate["answer_requirements"].model_dump(),
            "supported": candidate["supported"],
            "retrieval_complete": (
                candidate["retrieval"].completeness.retrieval_complete
            ),
            "missing_evidence": (
                candidate["retrieval"].completeness.missing_evidence
            ),
            "response": candidate["response"],
            "validation": candidate["validation"].model_dump(),
            "guardrails": candidate["guardrails"],
            "diagnostics": candidate["diagnostics"],
            "error": candidate["error"],
            "fallback_reason": candidate["fallback_reason"],
            "latency_ms": candidate["latency"].total_evidence_ms,
            "latency": candidate["latency"].model_dump(),
            "evidence_bundle": candidate["evidence_bundle"].model_dump(),
        }
    except Exception as error:
        logger.info(
            "EVIDENCE_PIPELINE_FALLBACK reason=%s",
            error,
        )

        return {
            "intent": None,
            "retrieval_complete": False,
            "missing_evidence": [str(error)],
            "response": None,
            "validation": EvidenceValidationResult(valid=False).model_dump(),
            "guardrails": None,
            "error": str(error),
            "fallback_reason": "WRITER_ERROR",
            "latency_ms": 0,
            "latency": EvidenceLatencyBreakdown().model_dump(),
            "evidence_bundle": None,
        }
