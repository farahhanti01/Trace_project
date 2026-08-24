import re
from typing import Any

from pydantic import BaseModel, Field

from app.database import document_content_units_collection
from app.services.generic_question_classifier_service import (
    QueryPlan,
    clean_text,
    normalize_for_search,
)
from app.services.retrieval_service import (
    select_relevant_sections,
)


class RetrievalCompleteness(BaseModel):
    retrieval_complete: bool = True
    missing_evidence: list[str] = Field(default_factory=list)


class AdaptiveRetrievalResult(BaseModel):
    sections: list[dict[str, Any]] = Field(default_factory=list)
    content_units: list[dict[str, Any]] = Field(default_factory=list)
    strategies_used: list[str] = Field(default_factory=list)
    expansions: list[dict[str, Any]] = Field(default_factory=list)
    completeness: RetrievalCompleteness = Field(default_factory=RetrievalCompleteness)


def unique_dicts_by_key(
    values: list[dict[str, Any]],
    key_name: str,
) -> list[dict[str, Any]]:
    seen = set()
    unique_values = []

    for value in values:
        key = value.get(key_name)

        if not key or key in seen:
            continue

        seen.add(key)
        unique_values.append(value)

    return unique_values


def first_entity_value(
    entities: dict[str, Any],
    key: str,
) -> str | None:
    value = entities.get(key)

    if isinstance(value, list):
        return str(value[0]) if value else None

    return str(value) if value else None


def query_terms_from_entities(
    question: str,
    entities: dict[str, Any],
) -> list[str]:
    terms = []
    terms.extend(entities.get("concepts") or [])
    terms.extend(entities.get("terms") or [])
    terms.extend(entities.get("message_types") or [])
    terms.extend(entities.get("field_numbers") or [])

    if not terms:
        terms = re.findall(r"[a-zA-Z0-9_.-]{3,}", normalize_for_search(question))

    return list(dict.fromkeys(clean_text(term) for term in terms if clean_text(term)))


def section_source_chunk_id(section: dict[str, Any]) -> str | None:
    document_id = section.get("document_id")
    section_index = section.get("section_index")
    chunk_index = section.get("chunk_index")

    if document_id is None or (section_index is None and chunk_index is None):
        return None

    return f"{document_id}:{section_index}:{chunk_index}"


def section_matches_field(
    section: dict[str, Any],
    field_number: str,
) -> bool:
    if str(section.get("field_number") or "") == field_number:
        return True

    searchable = normalize_for_search(
        " ".join([
            section.get("heading") or "",
            section.get("text") or "",
        ])
    )
    compact = searchable.replace(" ", "")
    compact_field = field_number.lstrip("0")

    return (
        f"field {compact_field}" in searchable
        or f"field{compact_field}" in compact
        or f"field {field_number}" in searchable
        or f"field{field_number}" in compact
    )


def section_primary_field_matches(
    section: dict[str, Any],
    field_number: str,
) -> bool:
    return str(section.get("field_number") or "") == field_number


def is_navigation_section(section: dict[str, Any]) -> bool:
    heading = normalize_for_search(section.get("heading") or "")
    text = normalize_for_search(section.get("text") or "")

    if heading in {"table of contents", "contents", "list of tables", "index"}:
        return True

    if heading.startswith("table of contents") or heading.startswith("list of tables"):
        return True

    return bool(
        re.search(
            r"\b(table of contents|list of tables|copyright|all rights reserved|visa confidential)\b",
            text[:1_200],
        )
    )


def sort_sections_by_document_order(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        sections,
        key=lambda section: (
            section.get("document_id") or "",
            section.get("section_index")
            if section.get("section_index") is not None
            else 10**9,
            section.get("chunk_index")
            if section.get("chunk_index") is not None
            else 10**9,
        ),
    )


def score_unit(
    unit: dict[str, Any],
    *,
    question: str,
    plan: QueryPlan,
) -> float:
    unit_entities = unit.get("entities") or {}
    primary_entities = unit.get("primary_entities") or {}
    content_type = unit.get("content_type")
    searchable = normalize_for_search(
        " ".join([
            unit.get("content") or "",
            ((unit.get("hierarchy") or {}).get("title") or ""),
            str(unit_entities),
        ])
    )
    score = 0.0

    if content_type in plan.target_content_types:
        score += 2.0

    for field_number in plan.entities.get("field_numbers") or []:
        if unit_entities.get("field_number") == field_number:
            score += 5.0

        if field_number in primary_entities.get("field_numbers", []):
            score += 2.0

    for code in plan.entities.get("codes") or []:
        if unit_entities.get("code") == code:
            score += 8.0

    for message_type in plan.entities.get("message_types") or []:
        if unit_entities.get("message_type") == message_type:
            score += 5.0
        elif message_type in searchable:
            score += 1.5

    for term in query_terms_from_entities(question, plan.entities):
        normalized_term = normalize_for_search(term)

        if not normalized_term:
            continue

        if normalized_term in searchable:
            score += 1.0

    if unit.get("document_zone") in {"front_matter", "index"}:
        score -= 5.0

    return round(score, 4)


class AdaptiveRetriever:
    """Retrieval adaptatif debug, sans remplacer le pipeline final."""

    @staticmethod
    async def retrieve(
        *,
        question: str,
        sections: list[dict[str, Any]],
        document_ids: list[str],
        plan: QueryPlan,
        sections_limit: int | None = None,
        units_limit: int | None = None,
    ) -> AdaptiveRetrievalResult:
        strategies_used = []
        expansions = []
        max_sections = sections_limit or plan.max_sections
        max_units = units_limit or plan.max_units
        selected_sections = await AdaptiveRetriever.retrieve_sections(
            question=question,
            sections=sections,
            plan=plan,
            limit=max_sections,
        )
        strategies_used.append("textual_sections")
        units = await AdaptiveRetriever.retrieve_units(
            question=question,
            document_ids=document_ids,
            plan=plan,
            selected_sections=selected_sections,
            limit=max_units,
            expansions=expansions,
            strategies_used=strategies_used,
        )
        completeness = AdaptiveRetriever.evaluate_completeness(
            plan=plan,
            sections=selected_sections,
            units=units,
        )

        return AdaptiveRetrievalResult(
            sections=selected_sections,
            content_units=units,
            strategies_used=strategies_used,
            expansions=expansions,
            completeness=completeness,
        )

    @staticmethod
    async def retrieve_sections(
        *,
        question: str,
        sections: list[dict[str, Any]],
        plan: QueryPlan,
        limit: int,
    ) -> list[dict[str, Any]]:
        field_number = first_entity_value(plan.entities, "field_numbers")
        chapter = plan.entities.get("chapter")

        if plan.intent == "SUMMARY" and chapter:
            selected = [
                {
                    **section,
                    "adaptive_score": 10.0,
                    "adaptive_reason": "chapter hierarchy match",
                }
                for section in sections
                if normalize_for_search(section.get("heading") or "").startswith(
                    f"chapter {chapter}"
                )
            ]

            return sort_sections_by_document_order(selected)[:limit]

        if field_number and plan.intent in {
            "FIELD_LOOKUP",
            "VALUE_LOOKUP",
            "TABLE_LOOKUP",
            "RULE_LOOKUP",
        }:
            primary_selected = [
                {
                    **section,
                    "adaptive_score": 10.0,
                    "adaptive_reason": "primary field section",
                }
                for section in sections
                if section_primary_field_matches(section, field_number)
                and not is_navigation_section(section)
            ]
            mention_selected = [
                {
                    **section,
                    "adaptive_score": 7.0,
                    "adaptive_reason": "exact field section",
                }
                for section in sections
                if section_matches_field(section, field_number)
                and not section_primary_field_matches(section, field_number)
                and not is_navigation_section(section)
            ]
            selected = [*primary_selected, *mention_selected]

            if selected:
                selected.sort(
                    key=lambda section: (
                        section.get("adaptive_score") or 0,
                        -(
                            section.get("section_index")
                            if section.get("section_index") is not None
                            else 10**9
                        ),
                        -(
                            section.get("chunk_index")
                            if section.get("chunk_index") is not None
                            else 10**9
                        ),
                    ),
                    reverse=True,
                )
                return selected[:limit]

        selected = await select_relevant_sections(
            question=question,
            sections=sections,
            limit=limit,
        )

        return [
            {
                **section,
                "adaptive_score": section.get("rerank_score")
                or section.get("retrieval_score")
                or 0,
                "adaptive_reason": "existing retrieval_service",
            }
            for section in selected
        ]

    @staticmethod
    async def retrieve_units(
        *,
        question: str,
        document_ids: list[str],
        plan: QueryPlan,
        selected_sections: list[dict[str, Any]],
        limit: int,
        expansions: list[dict[str, Any]],
        strategies_used: list[str],
    ) -> list[dict[str, Any]]:
        if not document_ids:
            return []

        base_query: dict[str, Any] = {
            "document_id": {"$in": document_ids},
        }
        field_number = first_entity_value(plan.entities, "field_numbers")
        code = first_entity_value(plan.entities, "codes")
        message_type = first_entity_value(plan.entities, "message_types")

        exact_query = dict(base_query)

        if plan.intent == "VALUE_LOOKUP" and field_number and code:
            exact_query.update({
                "entities.field_number": field_number,
                "entities.code": code,
                "content_type": {"$in": ["code_mapping", "table_row"]},
            })
            strategies_used.append("structured_exact_lookup")
            units = await document_content_units_collection.find(exact_query).to_list(
                length=limit
            )
        elif plan.intent == "TABLE_LOOKUP" and field_number:
            exact_query.update({
                "entities.field_number": field_number,
                "content_type": {"$in": ["table", "table_row", "code_mapping"]},
            })
            strategies_used.append("structured_table_lookup")
            units = await document_content_units_collection.find(exact_query).to_list(
                length=max(limit, 500)
            )
        elif field_number:
            exact_query.update({
                "entities.field_number": field_number,
                "content_type": {"$in": plan.target_content_types},
            })
            strategies_used.append("structured_field_lookup")
            units = await document_content_units_collection.find(exact_query).to_list(
                length=limit
            )
        elif plan.intent == "SUMMARY" and plan.entities.get("chapter"):
            exact_query.update({
                "hierarchy.chapter": {
                    "$regex": f"^Chapter {re.escape(str(plan.entities['chapter']))}\\b",
                    "$options": "i",
                },
            })
            strategies_used.append("hierarchical_lookup")
            units = await document_content_units_collection.find(exact_query).to_list(
                length=limit
            )
        elif message_type and plan.intent == "COMPARISON":
            exact_query.update({
                "$or": [
                    {"entities.message_type": message_type},
                    {"content": {"$regex": re.escape(message_type), "$options": "i"}},
                ],
            })
            strategies_used.append("message_type_lookup")
            units = await document_content_units_collection.find(exact_query).to_list(
                length=limit
            )
        else:
            units = await AdaptiveRetriever.units_from_selected_sections(
                document_ids=document_ids,
                selected_sections=selected_sections,
                limit=limit,
            )
            strategies_used.append("selected_section_units")

        if not units and plan.intent in {"DEFINITION", "LOCATION", "EXPLANATION", "PROCEDURE", "CROSS_SECTION", "DIAGNOSTIC_SUPPORT"}:
            units = await AdaptiveRetriever.lexical_units(
                document_ids=document_ids,
                question=question,
                plan=plan,
                limit=limit,
            )
            strategies_used.append("lexical_unit_fallback")

        units = await AdaptiveRetriever.expand_units(
            document_ids=document_ids,
            units=units,
            plan=plan,
            limit=limit,
            expansions=expansions,
        )
        scored_units = []

        for unit in units:
            score = score_unit(unit, question=question, plan=plan)

            if score <= -1:
                continue

            scored_units.append({
                **unit,
                "adaptive_score": score,
                "adaptive_reason": AdaptiveRetriever.reason_for_unit(unit, plan),
            })

        scored_units.sort(
            key=lambda unit: (
                unit.get("adaptive_score", 0),
                -(
                    unit.get("document_order")
                    if unit.get("document_order") is not None
                    else 10**9
                ),
            ),
            reverse=True,
        )

        if plan.intent in {"SUMMARY", "TABLE_LOOKUP"}:
            scored_units.sort(
                key=lambda unit: (
                    unit.get("document_id") or "",
                    unit.get("document_order")
                    if unit.get("document_order") is not None
                    else 10**9,
                )
            )

        return unique_dicts_by_key(scored_units, "unit_id")[:limit]

    @staticmethod
    async def units_from_selected_sections(
        *,
        document_ids: list[str],
        selected_sections: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        source_section_ids = [
            str(section.get("source_section_id"))
            for section in selected_sections
            if section.get("source_section_id")
        ]
        source_chunk_ids = [
            chunk_id
            for chunk_id in [
                section_source_chunk_id(section)
                for section in selected_sections
            ]
            if chunk_id
        ]
        filters = []

        if source_section_ids:
            filters.append({"source_section_id": {"$in": source_section_ids}})

        if source_chunk_ids:
            filters.append({"source_chunk_id": {"$in": source_chunk_ids}})

        if not filters:
            return []

        return await document_content_units_collection.find({
            "document_id": {"$in": document_ids},
            "$or": filters,
        }).sort(
            [
                ("document_id", 1),
                ("document_order", 1),
            ]
        ).to_list(length=limit)

    @staticmethod
    async def lexical_units(
        *,
        document_ids: list[str],
        question: str,
        plan: QueryPlan,
        limit: int,
    ) -> list[dict[str, Any]]:
        terms = query_terms_from_entities(question, plan.entities)
        regex_terms = [
            re.escape(term)
            for term in terms
            if len(term) >= 3
        ][:8]

        if not regex_terms:
            return []

        regex = "|".join(regex_terms)

        return await document_content_units_collection.find({
            "document_id": {"$in": document_ids},
            "document_zone": {"$nin": ["front_matter", "index"]},
            "$or": [
                {"content": {"$regex": regex, "$options": "i"}},
                {"hierarchy.title": {"$regex": regex, "$options": "i"}},
            ],
        }).sort(
            [
                ("document_id", 1),
                ("document_order", 1),
            ]
        ).to_list(length=limit)

    @staticmethod
    async def expand_units(
        *,
        document_ids: list[str],
        units: list[dict[str, Any]],
        plan: QueryPlan,
        limit: int,
        expansions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not units:
            return []

        existing_ids = {unit.get("unit_id") for unit in units}
        expanded = list(units)
        parent_ids = {
            unit.get("parent_unit_id")
            for unit in units
            if unit.get("parent_unit_id")
        }
        table_ids = {
            unit.get("unit_id")
            for unit in units
            if unit.get("content_type") == "table"
        }
        parent_ids = {value for value in [*parent_ids, *table_ids] if value}

        if parent_ids and plan.intent in {"VALUE_LOOKUP", "TABLE_LOOKUP"}:
            rows = await document_content_units_collection.find({
                "document_id": {"$in": document_ids},
                "$or": [
                    {"unit_id": {"$in": list(parent_ids)}},
                    {"parent_unit_id": {"$in": list(parent_ids)}},
                ],
            }).sort(
                [
                    ("document_id", 1),
                    ("document_order", 1),
                ]
            ).to_list(length=max(limit, 500 if plan.require_complete_table else limit))
            expansions.append({
                "type": "same_table",
                "parent_unit_ids": list(parent_ids),
                "count": len(rows),
            })

            for row in rows:
                if row.get("unit_id") not in existing_ids:
                    expanded.append(row)
                    existing_ids.add(row.get("unit_id"))

        if plan.intent in {
            "FIELD_LOOKUP",
            "EXPLANATION",
            "PROCEDURE",
            "RULE_LOOKUP",
        } and len(expanded) < limit:
            source_chunk_ids = list({
                unit.get("source_chunk_id")
                for unit in units
                if unit.get("source_chunk_id")
            })

            if source_chunk_ids:
                neighbors = await document_content_units_collection.find({
                    "document_id": {"$in": document_ids},
                    "source_chunk_id": {"$in": source_chunk_ids},
                }).sort(
                    [
                        ("document_id", 1),
                        ("document_order", 1),
                    ]
                ).to_list(length=limit)
                expansions.append({
                    "type": "same_section",
                    "source_chunk_ids": source_chunk_ids,
                    "count": len(neighbors),
                })

                for unit in neighbors:
                    if unit.get("unit_id") not in existing_ids:
                        expanded.append(unit)
                        existing_ids.add(unit.get("unit_id"))

        return expanded

    @staticmethod
    def reason_for_unit(
        unit: dict[str, Any],
        plan: QueryPlan,
    ) -> str:
        unit_entities = unit.get("entities") or {}

        if plan.entities.get("codes") and unit_entities.get("code") in plan.entities["codes"]:
            return "exact code match"

        if plan.entities.get("field_numbers") and unit_entities.get("field_number") in plan.entities["field_numbers"]:
            return "exact field match"

        if unit.get("content_type") in plan.target_content_types:
            return "target content_type"

        return "context expansion"

    @staticmethod
    def evaluate_completeness(
        *,
        plan: QueryPlan,
        sections: list[dict[str, Any]],
        units: list[dict[str, Any]],
    ) -> RetrievalCompleteness:
        missing = []
        field_number = first_entity_value(plan.entities, "field_numbers")
        code = first_entity_value(plan.entities, "codes")

        if plan.intent == "VALUE_LOOKUP":
            has_code = any(
                (unit.get("entities") or {}).get("code") == code
                and unit.get("content_type") in {"code_mapping", "table_row"}
                for unit in units
            )

            if field_number and code and not has_code:
                missing.append(f"exact mapping for field {field_number} code {code}")

        if plan.intent == "TABLE_LOOKUP":
            has_table_rows = any(
                unit.get("content_type") in {"table_row", "code_mapping"}
                for unit in units
            )

            if not has_table_rows:
                missing.append("table rows or code mappings")

        if plan.intent == "COMPARISON":
            targets = plan.comparison_targets

            for target in targets:
                target_value = next(iter(target.values()), None)

                if not target_value:
                    continue

                found = any(
                    target_value in normalize_for_search(
                        " ".join([
                            unit.get("content") or "",
                            str(unit.get("entities") or {}),
                        ])
                    )
                    for unit in units
                )

                if not found:
                    missing.append(f"comparison target {target_value}")

        if plan.intent == "SUMMARY" and plan.entities.get("chapter") and not units:
            missing.append(f"chapter {plan.entities['chapter']} content units")

        if not units and not sections:
            missing.append("retrieved evidence")

        return RetrievalCompleteness(
            retrieval_complete=not missing,
            missing_evidence=missing,
        )
