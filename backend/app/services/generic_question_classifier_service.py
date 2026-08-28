import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, Field


DocumentationIntent = Literal[
    "DEFINITION",
    "EXPLANATION",
    "FIELD_LOOKUP",
    "VALUE_LOOKUP",
    "TABLE_LOOKUP",
    "RULE_LOOKUP",
    "PROCEDURE",
    "COMPARISON",
    "SUMMARY",
    "CROSS_SECTION",
    "LOCATION",
    "DIAGNOSTIC_SUPPORT",
    "GENERAL_QA",
]


class GenericEntities(BaseModel):
    field_numbers: list[str] = Field(default_factory=list)
    codes: list[str] = Field(default_factory=list)
    message_types: list[str] = Field(default_factory=list)
    chapter: str | None = None
    section: str | None = None
    concepts: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    primary_entities: dict[str, Any] = Field(default_factory=dict)
    referenced_entities: dict[str, Any] = Field(default_factory=dict)


class ClassificationResult(BaseModel):
    intent: DocumentationIntent
    confidence: float = 0.0
    entities: GenericEntities = Field(default_factory=GenericEntities)
    reasons: list[str] = Field(default_factory=list)


class QueryPlan(BaseModel):
    intent: DocumentationIntent
    entities: dict[str, Any] = Field(default_factory=dict)
    strategies: list[str] = Field(default_factory=list)
    target_content_types: list[str] = Field(default_factory=list)
    require_complete_table: bool = False
    require_hierarchy: bool = False
    require_multiple_targets: bool = False
    comparison_targets: list[dict[str, Any]] = Field(default_factory=list)
    max_sections: int = 8
    max_units: int = 40


STOP_TERMS = {
    "avec",
    "dans",
    "des",
    "donne",
    "explique",
    "field",
    "fields",
    "pour",
    "que",
    "quel",
    "quelle",
    "quels",
    "quelles",
    "represente",
    "signifie",
    "sont",
    "the",
    "une",
    "valeurs",
}


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_for_search(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", clean_text(value).lower())

    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def normalize_field_number(raw_number: str) -> str:
    if "." in raw_number:
        left, right = raw_number.split(".", 1)
        return f"{int(left):03d}.{right}" if left.isdigit() else raw_number

    return f"{int(raw_number):03d}" if raw_number.isdigit() else raw_number


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def extract_explicit_requested_codes(
    question: str,
    *,
    field_numbers: list[str],
) -> list[str]:
    """Extract codes listed by the user for a known field.

    This intentionally does not treat every number in a question as a code.
    It only activates when a field is already explicit and the wording asks
    for codes, values or meanings.
    """
    if not field_numbers:
        return []

    normalized = normalize_for_search(question)

    if not re.search(
        r"\b(codes?|valeurs?|values?|significations?|meanings?)\b",
        normalized,
    ):
        return []

    field_aliases = set(field_numbers)
    field_aliases.update(
        value.lstrip("0") or value
        for value in field_numbers
    )
    codes = []

    for token in re.findall(r"\b[A-Za-z0-9]{2,4}\b", question):
        code = token.upper()

        if code in field_aliases:
            continue

        if code in {"AN", "PDF", "MTI", "ISO", "HSM"}:
            continue

        if not re.search(r"\d", code):
            continue

        codes.append(code)

    return unique(codes)


def extract_terms(question: str) -> list[str]:
    terms = re.findall(r"[a-zA-Z0-9_.-]{3,}", normalize_for_search(question))

    return unique([
        term
        for term in terms
        if term not in STOP_TERMS
    ])


def extract_generic_entities(question: str) -> GenericEntities:
    normalized = normalize_for_search(question)
    field_numbers = []
    codes = []

    for match in re.finditer(
        r"\b(?:field|fld|champ|de|data\s+element)\s*\(?0*(\d{1,3}(?:\.\d+)?)\)?",
        normalized,
        flags=re.I,
    ):
        field_numbers.append(normalize_field_number(match.group(1)))

    for match in re.finditer(
        r"\b(?:field|fld|champ|de)?\s*0*(\d{1,3}(?:\.\d+)?)\s*=\s*([A-Z0-9]{1,8})",
        question,
        flags=re.I,
    ):
        field_numbers.append(normalize_field_number(match.group(1)))
        codes.append(match.group(2).upper())

    codes.extend(
        extract_explicit_requested_codes(
            question,
            field_numbers=unique(field_numbers),
        )
    )

    message_types = re.findall(
        r"\b(?:0[1248]00|0[1248]10|1[1248]10|0100|0110|0200|0210)\b",
        normalized,
    )
    chapter_match = re.search(r"\b(?:chapter|chapitre)\s+(\d+)\b", normalized)
    section_match = re.search(r"\b(?:section)\s+(\d+(?:\.\d+)*)\b", normalized)
    quoted = re.findall(r'"([^"]+)"|' r"'([^']+)'", question)
    quoted_terms = [
        clean_text(left or right)
        for left, right in quoted
        if clean_text(left or right)
    ]
    uppercase_concepts = [
        token
        for token in re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", question)
        if not re.fullmatch(r"\d+", token)
        and token.upper() not in {"MTI", "PDF"}
    ]
    concept_candidates = quoted_terms + uppercase_concepts

    if not concept_candidates and not field_numbers and not message_types:
        concept_match = re.search(
            r"\b(?:qu'?est-ce que|what is|explique|explain|ou parle-t-on de|où parle-t-on de)\s+(.+?)[?.]?$",
            normalized,
            flags=re.I,
        )

        if concept_match:
            concept = clean_text(concept_match.group(1))

            if concept and len(concept.split()) <= 5:
                concept_candidates.append(concept)

    primary_entities: dict[str, Any] = {}

    if field_numbers:
        primary_entities["field_numbers"] = unique(field_numbers)

    if codes:
        primary_entities["codes"] = unique(codes)

    if message_types:
        primary_entities["message_types"] = unique(message_types)

    if chapter_match:
        primary_entities["chapter"] = chapter_match.group(1)

    if section_match:
        primary_entities["section"] = section_match.group(1)

    if concept_candidates:
        primary_entities["concepts"] = unique(concept_candidates)

    return GenericEntities(
        field_numbers=unique(field_numbers),
        codes=unique(codes),
        message_types=unique(message_types),
        chapter=chapter_match.group(1) if chapter_match else None,
        section=section_match.group(1) if section_match else None,
        concepts=unique(concept_candidates),
        terms=extract_terms(question),
        primary_entities=primary_entities,
        referenced_entities={},
    )


class GenericQuestionClassifier:
    """Classe les questions documentaires sans specialiser le moteur sur ISO."""

    @staticmethod
    def classify(question: str) -> ClassificationResult:
        normalized = normalize_for_search(question)
        entities = extract_generic_entities(question)
        reasons: list[str] = []
        intent: DocumentationIntent = "GENERAL_QA"
        confidence = 0.58

        asks_table = bool(re.search(
            r"\b(tableau|table|codes?|valeurs?|valid values|significations?)\b",
            normalized,
        ))
        asks_rule = bool(re.search(
            r"\b(obligatoire|required|mandatory|condition|rule|regle|reject|rejet|edits?)\b",
            normalized,
        ))
        asks_location = bool(re.search(
            r"\b(ou parle|où parle|parle-t-on|where|localise|trouve|page|section)\b",
            normalized,
        ))
        asks_summary = bool(re.search(
            r"\b(resume|resumer|summarize|overview|chapitre|chapter)\b",
            normalized,
        ))
        asks_comparison = bool(re.search(
            r"\b(difference|différence|compare|comparaison|vs|versus|entre)\b",
            normalized,
        ))
        asks_procedure = bool(re.search(
            r"\b(comment fonctionne|how does|procedure|process|workflow|reversal|annulation)\b",
            normalized,
        ))
        asks_definition = bool(re.search(
            r"\b(qu'?est-ce que|what is|definition|definir|définir|c'est quoi)\b",
            normalized,
        ))
        asks_diagnostic = bool(re.search(
            r"\b(refus|decline|declined|echec|échec|diagnostic|comprendre|quoi regarder)\b",
            normalized,
        ))

        if asks_comparison and (
            len(entities.message_types) >= 2
            or len(entities.concepts) >= 2
            or len(entities.field_numbers) >= 2
        ):
            intent = "COMPARISON"
            confidence = 0.9
            reasons.append("comparison wording with multiple targets")
        elif asks_location:
            intent = "LOCATION"
            confidence = 0.86
            reasons.append("location/page request")
        elif asks_summary:
            intent = "SUMMARY"
            confidence = 0.84
            reasons.append("summary or chapter request")
        elif entities.field_numbers and entities.codes:
            intent = "VALUE_LOOKUP"
            confidence = 0.94
            reasons.append("field=value pattern")
        elif entities.field_numbers and asks_rule:
            intent = "RULE_LOOKUP"
            confidence = 0.86
            reasons.append("field rule request")
        elif entities.field_numbers and asks_table:
            intent = "TABLE_LOOKUP"
            confidence = 0.92
            reasons.append("field table/codes request")
        elif entities.field_numbers:
            intent = "FIELD_LOOKUP"
            confidence = 0.88
            reasons.append("explicit field entity")
        elif asks_rule:
            intent = "RULE_LOOKUP"
            confidence = 0.8
            reasons.append("rule wording")
        elif asks_procedure:
            intent = "PROCEDURE"
            confidence = 0.78
            reasons.append("procedure/process wording")
        elif asks_definition:
            intent = "DEFINITION"
            confidence = 0.82
            reasons.append("definition wording")
        elif asks_diagnostic:
            intent = "DIAGNOSTIC_SUPPORT"
            confidence = 0.72
            reasons.append("diagnostic wording")
        elif re.search(r"\b(quels champs|which fields|plusieurs sections|identifier une transaction)\b", normalized):
            intent = "CROSS_SECTION"
            confidence = 0.74
            reasons.append("cross-section field request")
        elif re.search(r"\b(explique|explain|decris|décris|presente|présente)\b", normalized):
            intent = "EXPLANATION"
            confidence = 0.7
            reasons.append("general explanation wording")

        return ClassificationResult(
            intent=intent,
            confidence=confidence,
            entities=entities,
            reasons=reasons,
        )


class QueryPlanner:
    """Construit un plan de recherche inspectable pour le debug RAG."""

    @staticmethod
    def build(classification: ClassificationResult) -> QueryPlan:
        entities = classification.entities.model_dump()
        intent = classification.intent
        strategies: list[str]
        target_content_types: list[str]
        require_complete_table = False
        require_hierarchy = False
        require_multiple_targets = False
        comparison_targets: list[dict[str, Any]] = []
        max_sections = 8
        max_units = 40

        if intent == "VALUE_LOOKUP":
            strategies = [
                "structured_exact_lookup",
                "same_table_expansion",
                "same_section_expansion",
                "semantic_fallback",
            ]
            target_content_types = ["code_mapping", "table_row", "table"]
            max_units = 80
        elif intent == "FIELD_LOOKUP":
            strategies = [
                "structured_field_lookup",
                "same_section_expansion",
                "textual_field_sections",
                "semantic_fallback",
            ]
            target_content_types = [
                "field_description",
                "field_attribute",
                "field_usage",
                "definition",
            ]
        elif intent == "TABLE_LOOKUP":
            strategies = [
                "structured_table_lookup",
                "same_table_expansion",
                "textual_field_sections",
            ]
            target_content_types = ["table", "table_row", "code_mapping"]
            require_complete_table = True
            max_units = 500
        elif intent == "DEFINITION":
            strategies = [
                "lexical_concept_lookup",
                "definition_priority",
                "semantic_fallback",
            ]
            target_content_types = ["definition", "description", "paragraph", "heading"]
        elif intent == "EXPLANATION":
            strategies = [
                "semantic_retrieval",
                "heading_expansion",
                "same_section_expansion",
            ]
            target_content_types = [
                "definition",
                "description",
                "paragraph",
                "rule",
                "example",
            ]
        elif intent == "PROCEDURE":
            strategies = [
                "procedure_lookup",
                "workflow_lookup",
                "same_section_expansion",
                "semantic_fallback",
            ]
            target_content_types = [
                "procedure",
                "workflow",
                "rule",
                "exception",
                "paragraph",
            ]
        elif intent == "RULE_LOOKUP":
            strategies = [
                "rule_lookup",
                "field_usage_lookup",
                "same_section_expansion",
                "semantic_fallback",
            ]
            target_content_types = [
                "rule",
                "field_usage",
                "field_attribute",
                "exception",
                "note",
            ]
        elif intent == "COMPARISON":
            strategies = [
                "multi_target_structured_lookup",
                "multi_target_textual_retrieval",
                "semantic_fallback",
            ]
            target_content_types = [
                "definition",
                "description",
                "message_type",
                "paragraph",
                "rule",
            ]
            require_multiple_targets = True
            comparison_targets = [
                {"message_type": value}
                for value in entities.get("message_types", [])
            ]
            if not comparison_targets:
                comparison_targets = [
                    {"concept": value}
                    for value in entities.get("concepts", [])
                ]
            max_sections = 12
        elif intent == "SUMMARY":
            strategies = [
                "hierarchical_lookup",
                "document_order_preservation",
            ]
            target_content_types = [
                "heading",
                "paragraph",
                "definition",
                "description",
                "rule",
                "procedure",
                "workflow",
                "table",
            ]
            require_hierarchy = True
            max_units = 250
            max_sections = 20
        elif intent == "LOCATION":
            strategies = [
                "lexical_exact_lookup",
                "section_listing",
            ]
            target_content_types = ["heading", "definition", "description", "paragraph", "rule"]
        elif intent == "CROSS_SECTION":
            strategies = [
                "multi_query_retrieval",
                "semantic_retrieval",
                "reranking",
            ]
            target_content_types = ["definition", "description", "rule", "field_description"]
            max_sections = 12
        elif intent == "DIAGNOSTIC_SUPPORT":
            strategies = [
                "diagnostic_semantic_retrieval",
                "rules_and_codes_lookup",
                "semantic_fallback",
            ]
            target_content_types = [
                "rule",
                "code_mapping",
                "field_description",
                "workflow",
                "paragraph",
            ]
            max_sections = 12
        else:
            strategies = ["semantic_retrieval", "lexical_fallback"]
            target_content_types = ["definition", "description", "paragraph", "rule"]

        return QueryPlan(
            intent=intent,
            entities=entities,
            strategies=strategies,
            target_content_types=target_content_types,
            require_complete_table=require_complete_table,
            require_hierarchy=require_hierarchy,
            require_multiple_targets=require_multiple_targets,
            comparison_targets=comparison_targets,
            max_sections=max_sections,
            max_units=max_units,
        )
