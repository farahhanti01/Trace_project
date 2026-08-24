import re
import unicodedata
from typing import Any

from app.models.document_content import (
    ContentUnit,
    EvidenceBundle,
    EvidenceItem,
    EvidenceTable,
)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_for_search(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", clean_text(value).lower())

    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def extract_question_entities(question: str) -> dict[str, Any]:
    """Detecte des entites generiques utiles au debug EvidenceBundle."""
    normalized = normalize_for_search(question)
    entities: dict[str, Any] = {}

    field_match = re.search(
        r"\b(?:field|fld|champ|de|data element)\s*\(?0*(\d{1,3}(?:\.\d+)?)\)?",
        normalized,
    )

    if field_match:
        raw_field = field_match.group(1)
        entities["field_number"] = (
            f"{int(raw_field):03d}"
            if raw_field.isdigit()
            else raw_field
        )

    value_match = re.search(
        r"\b(?:field|fld|champ|de)?\s*0*(\d{1,3}(?:\.\d+)?)\s*=\s*([A-Z0-9]+)",
        normalized,
        flags=re.I,
    )

    if value_match:
        raw_field = value_match.group(1)
        entities.setdefault(
            "field_number",
            f"{int(raw_field):03d}" if raw_field.isdigit() else raw_field,
        )
        entities["code"] = value_match.group(2).upper()

    mti_match = re.search(r"\b(0[1248]00|1[1248]10|0200|0100|0110|0210)\b", normalized)

    if mti_match:
        entities["message_type"] = mti_match.group(1)

    quoted = re.findall(r'"([^"]+)"|' r"'([^']+)'", question)
    quoted_terms = [
        clean_text(left or right)
        for left, right in quoted
        if clean_text(left or right)
    ]

    if quoted_terms:
        entities["terms"] = quoted_terms

    return entities


def classify_documentation_question(question: str) -> str:
    """Classe generiquement une question documentaire pour le debug."""
    normalized = normalize_for_search(question)

    if re.search(r"\bcompare|difference|different|vs|versus\b", normalized):
        return "COMPARISON"

    if re.search(r"\bresume|resumer|summarize|overview|chapitre|chapter\b", normalized):
        return "SUMMARY"

    if re.search(r"\btableau|table|codes?|valeurs?|valid values\b", normalized):
        if re.search(r"\bfield|fld|champ|de\s*\d", normalized):
            return "TABLE_LOOKUP"

        return "VALUE_LOOKUP"

    if re.search(r"\b\d{1,3}(?:\.\d+)?\s*=\s*[a-z0-9]+\b", normalized):
        return "VALUE_LOOKUP"

    if re.search(r"\b(field|fld|champ|data element)\b", normalized):
        if "=" in normalized or re.search(r"\bsignifie|meaning|valeur\b", normalized):
            return "VALUE_LOOKUP"

        return "FIELD_LOOKUP"

    if re.search(r"\bcomment fonctionne|how does|procedure|process|reversal\b", normalized):
        return "PROCEDURE"

    if re.search(r"\bqu'?est-ce que|what is|definition|definir|definis\b", normalized):
        return "DEFINITION"

    if re.search(r"\bobligatoire|required|must|rule|regle\b", normalized):
        return "RULE_LOOKUP"

    return "EXPLANATION"


def unit_source_section(unit: ContentUnit) -> str | None:
    return (
        unit.hierarchy.subsection
        or unit.hierarchy.section
        or unit.hierarchy.title
    )


def evidence_item_from_unit(unit: ContentUnit) -> EvidenceItem:
    return EvidenceItem(
        content=unit.content,
        source_unit_id=unit.unit_id,
        document_id=unit.document_id,
        pdf_page=unit.source_location.pdf_page,
        printed_page=unit.source_location.printed_page,
        section=unit_source_section(unit),
        content_type=unit.content_type,
        entity_alignment=getattr(unit, "entity_alignment", None),
        confidence=unit.parsing_confidence,
    )


def unit_matches_entities(
    unit: ContentUnit,
    entities: dict[str, Any],
) -> bool:
    if not entities:
        return True

    unit_entities = unit.entities or {}

    for key in ("field_number", "code", "message_type"):
        expected = entities.get(key)

        if not expected:
            continue

        observed = unit_entities.get(key)

        if observed is None:
            return False

        if normalize_for_search(observed) != normalize_for_search(expected):
            return False

    return True


def unit_matches_query(
    unit: ContentUnit,
    query: str,
) -> bool:
    normalized_query = normalize_for_search(query)
    searchable = normalize_for_search(" ".join([
        unit.content,
        unit.hierarchy.title or "",
        str(unit.entities or {}),
    ]))

    query_terms = [
        term
        for term in re.findall(r"[a-zA-Z0-9_.-]{3,}", normalized_query)
        if term not in {"the", "and", "for", "dans", "pour", "les", "des"}
    ]

    if not query_terms:
        return True

    return any(term in searchable for term in query_terms)


def sort_units(units: list[ContentUnit]) -> list[ContentUnit]:
    return sorted(
        units,
        key=lambda unit: (
            unit.source or "",
            unit.document_order
            if unit.document_order is not None
            else 10**9,
            unit.unit_id,
        ),
    )


def group_table_units(units: list[ContentUnit]) -> list[EvidenceTable]:
    by_parent: dict[str, dict[str, Any]] = {}
    rows_by_parent: dict[str, list[ContentUnit]] = {}

    for unit in units:
        if unit.content_type == "table":
            by_parent[unit.unit_id] = {
                "title": unit.hierarchy.title or unit.content or "Table",
                "source_unit_ids": [unit.unit_id],
            }
        elif unit.content_type == "table_row" and unit.parent_unit_id:
            rows_by_parent.setdefault(unit.parent_unit_id, []).append(unit)

    tables = []

    for parent_id, metadata in by_parent.items():
        row_units = rows_by_parent.get(parent_id, [])

        if not row_units:
            continue

        row_cells = []
        max_columns = 0

        for row_unit in row_units:
            cells = [
                clean_text(cell)
                for cell in row_unit.content.split("|")
            ]
            row_cells.append((row_unit, cells))
            max_columns = max(max_columns, len(cells))

        columns = [f"col_{index + 1}" for index in range(max_columns)]
        rows = []

        for row_unit, cells in row_cells:
            rows.append({
                **{
                    columns[index]: cells[index] if index < len(cells) else ""
                    for index in range(max_columns)
                },
                "source_unit_id": row_unit.unit_id,
            })

        tables.append(
            EvidenceTable(
                title=metadata["title"],
                columns=columns,
                rows=rows,
                source_unit_ids=[
                    *metadata["source_unit_ids"],
                    *[row_unit.unit_id for row_unit, _ in row_cells],
                ],
            )
        )

    return tables


class EvidenceBuilder:
    """Construit une EvidenceBundle inspectable sans remplacer le pipeline RAG."""

    @staticmethod
    def build(
        *,
        question: str,
        intent: str | None = None,
        entities: dict[str, Any] | None = None,
        retrieved_sections: list[dict[str, Any]] | None = None,
        content_units: list[dict[str, Any] | ContentUnit] | None = None,
        limit: int | None = None,
    ) -> EvidenceBundle:
        resolved_intent = intent or classify_documentation_question(question)
        resolved_entities = {
            **extract_question_entities(question),
            **(entities or {}),
        }
        parsed_units = [
            unit
            if isinstance(unit, ContentUnit)
            else ContentUnit.model_validate(unit)
            for unit in content_units or []
        ]
        if resolved_entities:
            matching_units = [
                unit
                for unit in parsed_units
                if unit_matches_entities(unit, resolved_entities)
            ]
        else:
            matching_units = list(parsed_units)
        matching_units = sort_units(matching_units)

        if limit is not None:
            matching_units = matching_units[:limit]

        definitions = []
        facts = []
        rules = []
        examples = []
        process_steps = []
        other_evidence = []

        for unit in matching_units:
            item = evidence_item_from_unit(unit)

            if unit.content_type in {"definition", "description", "field_description"}:
                definitions.append(item)
            elif unit.content_type in {"rule", "valid_value"}:
                rules.append(item)
            elif unit.content_type == "example":
                examples.append(item)
            elif unit.content_type in {"procedure", "workflow", "list"}:
                process_steps.append(item)
            elif unit.content_type in {
                "field_attribute",
                "field_usage",
                "code_mapping",
                "paragraph",
                "table_row",
            }:
                facts.append(item)
            else:
                other_evidence.append(item)

        citations = []
        seen_citations = set()

        for item in [
            *definitions,
            *facts,
            *rules,
            *examples,
            *process_steps,
            *other_evidence,
        ]:
            key = (
                item.source_unit_id,
                item.pdf_page,
                item.printed_page,
            )

            if key in seen_citations:
                continue

            seen_citations.add(key)
            citations.append(item)

        return EvidenceBundle(
            query=question,
            intent=resolved_intent,
            entities=resolved_entities,
            target_entities=dict(entities or {}),
            definitions=definitions,
            facts=facts,
            rules=rules,
            tables=group_table_units(matching_units),
            examples=examples,
            process_steps=process_steps,
            other_evidence=other_evidence,
            retrieved_units=matching_units,
            citations=citations,
        )
