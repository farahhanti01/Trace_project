import json
import logging
import re
import time
import unicodedata
from typing import Any

from bson import ObjectId
from fastapi import HTTPException

from app.database import (
    document_sections_collection,
    documents_collection,
)
from app.services.hps_ai_service import (
    HpsAiConfigurationError,
    HpsAiRequestError,
    call_hps_ai,
)
from app.services.retrieval_service import (
    extract_relevant_excerpt,
    front_matter_penalty,
    select_relevant_sections as retrieve_relevant_sections,
)
from app.services.documentation_synthesis_service import (
    KnowledgeGenerationPipeline,
    code_rows_from_sections,
    dedupe_code_rows,
    question_requests_table,
    source_ids_from_rows,
)
from app.services.documentation_evidence_generation_service import (
    evidence_generation_enabled,
    evidence_shadow_enabled,
    evidence_table_lookup_generation_enabled,
    shadow_evidence_response,
    try_generate_evidence_response,
    try_generate_table_lookup_response,
)
from app.services.conversation_memory_service import (
    resolve_documentation_query,
    update_documentation_memory,
)
from app.services.file_type_service import REFERENCE_DOCUMENT_EXTENSIONS
from app.services.function_catalog_service import answer_function_question
from app.services.screenshot_analysis_service import answer_screenshot_question


logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARACTERS = 22_000
MAX_SELECTED_SECTIONS = 8
MAX_OVERVIEW_SECTIONS = 14
MAX_FIELD_FOCUSED_SECTIONS = 8
MAX_COMPACT_EVIDENCE_SECTIONS = 4
MAX_FIELD_EVIDENCE_SECTIONS = 12
UNSPECIFIED_DETAIL = "non specifie dans les extraits fournis"
DOCUMENT_SEARCH = "DOCUMENT_SEARCH"
DOCUMENT_OVERVIEW = "DOCUMENT_OVERVIEW"
ISO_FIELD_EXPLANATION = "ISO_FIELD_EXPLANATION"
ISO_VALUE_DECODING = "ISO_VALUE_DECODING"
HSM_RESPONSE_EXPLANATION = "HSM_RESPONSE_EXPLANATION"
HSM_MESSAGE_DECODING = "HSM_MESSAGE_DECODING"
FIELD_PATTERNS = (
    r"\bfield\s*0*(\d+(?:\.\d+)?)\b",
    r"\bfld\s*\(?0*(\d+(?:\.\d+)?)\)?",
    r"\bchamp\s*0*(\d+(?:\.\d+)?)\b",
    r"\bde\s*0*(\d+(?:\.\d+)?)\b",
    r"\bdata\s+element\s*0*(\d+(?:\.\d+)?)\b",
)
DOCUMENT_DISPLAY_NAMES = {
    "vip-system-base-i-tech-specs-volume-1.pdf": (
        "BASE I Technical Specifications, Volume 1"
    ),
}
DOCUMENTATION_RESPONSE_SCHEMA = (
    '{"summary": string, "sections": [{"title": string, "content": string, '
    '"source_ids": string[]}], "issues": [{"severity": "info|warning|error", '
    '"title": string, "detail": string|null}], "recommendations": string[], '
    '"references": [{"source_id": string|null, "source": string|null, '
    '"page": number|null, "pdf_page": number|null, "printed_page": string|null, '
    '"section": string|null, "sheet": string|null, "paragraph": number|null}]}'
)
GENERAL_DOCUMENTATION_PROMPT = (
    "You are Documentation Agent, a senior technical consultant. You are not "
    "a document search engine. Use the provided extracts as evidence only: "
    "read them, understand them, merge duplicates, and write a clear "
    "pedagogical answer. Never paste raw PDF paragraphs, OCR fragments, table "
    "rows, chapter titles, or source snippets as the answer. Return only valid "
    f"JSON with this schema: {DOCUMENTATION_RESPONSE_SCHEMA}. For a general "
    "documentation question, use sections titled: Contenu principal, Utilite, "
    "Limites, and any other useful business title. Answer in the user's "
    "language. If the user writes in French, all explanatory text must be in "
    "French; official technical names may stay in English. Every important "
    "idea must cite source_ids from the extracts. If the extracts do not "
    "support an answer, say that clearly without inventing."
)
ISO_FIELD_PROMPT = (
    "You are a senior Visa BASE I / ISO 8583 consultant. Use only the provided "
    "extracts as evidence, but do not copy them. Return only valid JSON with "
    f"this schema: {DOCUMENTATION_RESPONSE_SCHEMA}. The answer must be a "
    "written field explanation, not extracted snippets. Use sections titled "
    "exactly when supported: Role, Format, Structure, Utilisation, Exemple, "
    "Controles ou rejets. The summary must directly explain the requested "
    "field in 2 to 4 sentences. Explain and synthesize like ChatGPT would: "
    "merge facts from several pages, remove duplicates, and write natural "
    "technical prose. Do not use table of contents as proof. Keep every "
    "section focused on the requested field. Each section must include "
    "source_ids only from the extracts. If a part is missing, write that it is "
    "not specified in the provided extracts."
)
DOCUMENT_OVERVIEW_PROMPT = (
    "You are Documentation Agent, a senior technical documentation consultant. "
    "The user asks for a global overview. Use representative extracts across "
    "the document, not a single chunk. Return only valid JSON with this schema: "
    f"{DOCUMENTATION_RESPONSE_SCHEMA}. The sections must cover: Objectif, "
    "Organisation generale, Sujets techniques principaux, Utilite pour TRACE, "
    "Limites. Do not paste document excerpts. Write a natural synthesized "
    "overview in the user's language and cite source_ids for each section."
)
OVERVIEW_TERMS = (
    "resume",
    "resumer",
    "summarize",
    "summary",
    "vue globale",
    "presentation",
    "presente",
    "presenter",
    "de quoi parle",
    "overview",
    "document overview",
)
TABLE_OF_CONTENTS_TERMS = (
    "table of contents",
    "contents",
    "sommaire",
    "chapter",
    "chapitre",
)
TECHNICAL_OVERVIEW_TERMS = (
    "authorization",
    "authorisation",
    "chapter",
    "chapitre",
    "command",
    "commande",
    "data field",
    "field",
    "hsm",
    "iso",
    "message",
    "mti",
    "pin",
    "response code",
    "rules",
    "specification",
    "transaction",
)
FIELD_QUERY_EXPANSIONS = {
    "039": (
        "Field 39 Response Code authorization response approve decline "
        "action code response code values reject message"
    ),
    "39": (
        "Field 39 Response Code authorization response approve decline "
        "action code response code values reject message"
    ),
    "037": "Field 37 Retrieval Reference Number transaction matching reference",
    "37": "Field 37 Retrieval Reference Number transaction matching reference",
    "003": (
        "Field 3 Processing Code role usage attributes format length "
        "positions transaction type account type source destination values "
        "reject codes field edits authorization request response"
    ),
    "3": (
        "Field 3 Processing Code role usage attributes format length "
        "positions transaction type account type source destination values "
        "reject codes field edits authorization request response"
    ),
    "002": "Field 2 Primary Account Number PAN card number",
    "2": "Field 2 Primary Account Number PAN card number",
}
OVERVIEW_REJECT_CODE_TERMS = (
    "reject code",
    "code de rejet",
    "rejected with",
    "will be rejected",
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "au",
    "aux",
    "avec",
    "ce",
    "ces",
    "dans",
    "de",
    "des",
    "du",
    "en",
    "est",
    "et",
    "for",
    "is",
    "la",
    "le",
    "les",
    "of",
    "on",
    "ou",
    "pour",
    "que",
    "qui",
    "the",
    "to",
    "un",
    "une",
}


def normalize_for_search(value: str) -> str:
    """Normalise le texte pour ignorer la casse et les accents."""
    normalized = unicodedata.normalize("NFKD", value.lower())
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def extract_terms(question: str) -> list[str]:
    """Extrait les mots utiles de la question utilisateur."""
    words = re.findall(
        r"[a-zA-Z0-9_/-]{3,}",
        normalize_for_search(question),
    )

    return [
        word
        for word in dict.fromkeys(words)
        if word not in STOPWORDS
    ]


def extract_field_focuses(question: str) -> list[str]:
    """Detecte les champs exacts cites dans la question, ex: Field 126.10."""
    normalized_question = normalize_for_search(question)
    values = []

    for pattern in FIELD_PATTERNS:
        values.extend(
            re.findall(
                pattern,
                normalized_question,
                flags=re.IGNORECASE,
            )
        )

    return [
        f"field {value}"
        for value in dict.fromkeys(values)
    ]


def extract_field_value(
    question: str,
    field_number: str,
) -> str | None:
    """Extrait une valeur observee pour un champ, ex: Field 003 = 000000."""
    compact_field_number = str(int(field_number.split(".", 1)[0]))
    patterns = (
        rf"(?:field|fld|champ|de|data\s+element)\s*0*{compact_field_number}"
        r"\s*[:=]\s*\[?([A-Za-z0-9]+)",
        rf"FLD\s*\(?0*{compact_field_number}\)?.*?\[([^\]]+)\]",
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            question,
            flags=re.IGNORECASE,
        )

        if match:
            return match.group(1).strip()

    return None


def classify_intent(question: str) -> str:
    """Classe la question pour choisir retrieval et prompt specialises."""
    normalized_question = normalize_for_search(question)
    field_focuses = extract_field_focuses(question)

    if "to hsm" in normalized_question or "from hsm" in normalized_question:
        return HSM_MESSAGE_DECODING

    if re.search(r"\b[A-Z]{2}\d{2}\b", question):
        return HSM_RESPONSE_EXPLANATION

    if field_focuses:
        field_number = normalize_field_number(field_focuses[0])

        if (
            extract_field_value(question, field_number)
            or re.search(r"\b(valeur|value|decode|decod|signifie)\b", normalized_question)
        ):
            return ISO_VALUE_DECODING

        return ISO_FIELD_EXPLANATION

    if is_document_overview_question(question):
        return DOCUMENT_OVERVIEW

    return DOCUMENT_SEARCH


def prompt_for_intent(intent: str) -> str:
    """Retourne le prompt systeme court adapte a l'intention."""
    if intent in {ISO_FIELD_EXPLANATION, ISO_VALUE_DECODING}:
        return ISO_FIELD_PROMPT

    if intent == DOCUMENT_OVERVIEW:
        return DOCUMENT_OVERVIEW_PROMPT

    return GENERAL_DOCUMENTATION_PROMPT


def normalize_field_number(field_focus: str) -> str:
    """Convertit Field 039 en 39 pour matcher les libelles documentaires."""
    number = re.sub(
        r"\b(field|fld)\b|[()]",
        "",
        field_focus,
        flags=re.IGNORECASE,
    ).strip()

    if "." in number:
        left, right = number.split(".", 1)
        return f"{int(left)}.{right}" if left.isdigit() else number

    return str(int(number)) if number.isdigit() else number


def expand_documentation_question(question: str) -> str:
    """Enrichit les questions courtes pour guider le retrieval documentaire."""
    field_focuses = extract_field_focuses(question)

    if not field_focuses:
        return question

    additions = []

    for field_focus in field_focuses:
        normalized_number = normalize_field_number(field_focus)
        compact_number = normalized_number.replace(".", "")
        additions.append(
            f"Explique Field {normalized_number}: role, usage, presence, "
            "messages concernes, valeurs possibles, codes associes et pages "
            "exactes utilisees."
        )
        additions.append(
            FIELD_QUERY_EXPANSIONS.get(compact_number, "")
        )

    return " ".join(
        part
        for part in [
            question,
            *additions,
        ]
        if part
    )


def extract_exact_tokens(question: str) -> list[str]:
    """Extrait les tokens techniques exacts qui guident fortement le RAG."""
    normalized_question = normalize_for_search(question)
    tokens = re.findall(
        r"\b\d{4}\b|\bfield\s*\d+(?:\.\d+)?\b|\b\d+\.\d+\b",
        normalized_question,
    )

    return [
        re.sub(r"\s+", " ", token).strip()
        for token in dict.fromkeys(tokens)
    ]


def section_mentions_field_focus(
    section: dict[str, Any],
    field_focus: str,
) -> bool:
    """Verifie si une section retrouvee mentionne le champ demande."""
    section_text = normalize_for_search(
        " ".join(
            [
                section.get("heading") or "",
                section.get("text") or "",
            ]
        )
    )
    normalized_number = normalize_field_number(field_focus)

    if "." in normalized_number:
        left, right = normalized_number.split(".", 1)
        number_pattern = rf"0*{re.escape(left)}\.{re.escape(right)}"
        end_guard = r"(?!\d)"
    else:
        number_pattern = rf"0*{re.escape(normalized_number)}"
        end_guard = r"(?![\d.])"

    field_patterns = (
        rf"\b(?:data\s+)?field\s*\(?{number_pattern}\)?{end_guard}",
        rf"\bfld\s*\(?{number_pattern}\)?{end_guard}",
    )

    return any(
        re.search(pattern, section_text, flags=re.IGNORECASE)
        for pattern in field_patterns
    )


def focus_sections_on_exact_fields(
    question: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Garde les sections liees au champ exact demande."""
    field_focuses = extract_field_focuses(question)

    if not field_focuses:
        return sections

    focused_sections = [
        section
        for section in sections
        if any(
            section_mentions_field_focus(section, field_focus)
            for field_focus in field_focuses
        )
    ]

    if not focused_sections:
        return sections

    return focused_sections[:MAX_FIELD_FOCUSED_SECTIONS]


def exact_field_sections(
    question: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retrouve dans tous les documents les sections du champ demande."""
    field_focuses = extract_field_focuses(question)
    requested_field_numbers = {
        normalize_field_number(field_focus)
        for field_focus in field_focuses
    }

    if not field_focuses:
        return []

    matching_sections = [
        section
        for section in sections
        if (
            normalize_field_number(str(section.get("field_number") or ""))
            in requested_field_numbers
            if section.get("field_number")
            else any(
                section_mentions_field_focus(section, field_focus)
                for field_focus in field_focuses
            )
        )
    ]
    non_front_matter_sections = [
        section
        for section in matching_sections
        if not is_front_matter_field_section(section)
    ]
    primary_sections = [
        section
        for section in non_front_matter_sections
        if is_primary_field_definition_section(
            section=section,
            requested_field_numbers=requested_field_numbers,
        )
    ]

    return sorted(
        primary_sections or non_front_matter_sections or matching_sections,
        key=section_order_key,
    )


def is_front_matter_field_section(
    section: dict[str, Any],
) -> bool:
    """Exclut sommaires et listes qui ne prouvent pas la definition."""
    heading = normalize_for_search(section.get("heading") or "")

    return (
        "table of contents" in heading
        or "list of tables" in heading
        or heading.strip() == "contents"
    )


def is_primary_field_definition_section(
    section: dict[str, Any],
    requested_field_numbers: set[str],
) -> bool:
    """Priorise la vraie section Chapter 4 du champ demande."""
    heading = normalize_for_search(section.get("heading") or "")

    if "data field descriptions" not in heading:
        return False

    for requested_field in requested_field_numbers:
        base_number = requested_field.split(".", 1)[0]
        display_number = str(int(base_number)) if base_number.isdigit() else base_number
        pattern = rf"\bfield\s+0*{re.escape(display_number)}(?:\.\d+)?\s*[—-]"

        if re.search(pattern, heading):
            return True

    return False


def field_sections_with_neighbors(
    field_sections: list[dict[str, Any]],
    all_sections: list[dict[str, Any]],
    page_radius: int = 4,
) -> list[dict[str, Any]]:
    """Selectionne la section du champ jusqu'au debut du champ suivant."""
    if not field_sections:
        return []

    selected = []
    seen = set()
    anchor = field_sections[0]
    anchor_source = anchor.get("source")
    anchor_field_number = (
        normalize_field_number(str(anchor.get("field_number") or ""))
        if anchor.get("field_number")
        else None
    )
    source_sections = sorted(
        [
            section
            for section in all_sections
            if section.get("source") == anchor_source
        ],
        key=section_order_key,
    )
    start_index = next(
        (
            index
            for index, section in enumerate(source_sections)
            if (
                section.get("page"),
                section.get("section_index"),
                section.get("chunk_index"),
            )
            == (
                anchor.get("page"),
                anchor.get("section_index"),
                anchor.get("chunk_index"),
            )
        ),
        None,
    )

    if start_index is None:
        return field_sections[:MAX_FIELD_EVIDENCE_SECTIONS]

    for section in source_sections[start_index:]:
        section_field_number = (
            normalize_field_number(str(section.get("field_number") or ""))
            if section.get("field_number")
            else None
        )
        heading = normalize_for_search(section.get("heading") or "")

        if (
            selected
            and section_field_number
            and anchor_field_number
            and section_field_number != anchor_field_number
            and re.search(r"\bfield\s+\d+(?:\.\d+)?\s*(?:-|\u2013|\u2014)", heading)
        ):
            break

        identity = (
            section.get("source"),
            section.get("page"),
            section.get("section_index"),
            section.get("chunk_index"),
        )

        if identity in seen:
            continue

        seen.add(identity)
        selected.append(section)

        if len(selected) >= MAX_FIELD_EVIDENCE_SECTIONS:
            break

    return selected


def question_requests_values_or_table(question: str) -> bool:
    """Detecte les demandes de codes, valeurs ou tableaux officiels."""
    normalized = normalize_for_search(question)

    return bool(
        re.search(
            r"\btableau\b|\btable\b|\bcodes?\b|\bvaleurs?\b|"
            r"\bvalid values\b|\bsignification\b|\bsignifications\b",
            normalized,
        )
    )


def value_table_priority(section: dict[str, Any]) -> tuple[int, int]:
    """Priorise les pages qui portent des tables de valeurs du champ."""
    heading = normalize_for_search(section.get("heading") or "")
    text = normalize_for_search(section.get("text") or "")
    content_type = normalize_for_search(str(section.get("content_type") or ""))
    combined = f"{heading} {text[:1200]}"
    priority = 20

    if "field_values" in content_type:
        priority = min(priority, 1)

    if "valid values" in combined:
        priority = min(priority, 0)

    if re.search(r"\btable\s+\d+-\d+\b", combined):
        priority = min(priority, 0)

    if re.search(r"\bresponse codes?\b|\bcode\s+definition\b", combined):
        priority = min(priority, 0)

    if "field edits" in combined:
        priority = min(priority, 6)

    return (
        priority,
        int(section.get("page") or 0),
    )


def prioritize_value_table_sections(
    question: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Place les sous-sections de valeurs avant les paragraphes narratifs."""
    if not question_requests_values_or_table(question):
        return sections

    return sorted(
        sections,
        key=value_table_priority,
    )


def section_coverage_keys(
    question: str,
    section: dict[str, Any],
) -> set[str]:
    """Retourne les termes de la question couverts par une section."""
    searchable_text = normalize_for_search(
        " ".join(
            [
                section.get("heading") or "",
                section.get("evidence_excerpt") or "",
                section.get("text") or "",
            ]
        )
    )
    compact_text = searchable_text.replace(" ", "")
    keys = set()

    for token in extract_exact_tokens(question):
        compact_token = token.replace(" ", "")

        if token in searchable_text or compact_token in compact_text:
            keys.add(f"token:{token}")

    for term in extract_terms(question):
        if term in searchable_text:
            keys.add(f"term:{term}")

    return keys


def compact_sections_by_query_coverage(
    question: str,
    sections: list[dict[str, Any]],
    limit: int = MAX_COMPACT_EVIDENCE_SECTIONS,
) -> list[dict[str, Any]]:
    """Reduit les sections selectionnees sans perdre la couverture utile."""
    if len(sections) <= limit:
        return sections

    remaining = list(sections)
    selected = []
    covered_keys: set[str] = set()

    while remaining and len(selected) < limit:
        best_index = 0
        best_rank = (-1, -1.0, -1.0)

        for index, section in enumerate(remaining):
            keys = section_coverage_keys(
                question=question,
                section=section,
            )
            new_key_count = len(keys - covered_keys)
            score = float(
                section.get("rerank_score")
                or section.get("retrieval_score")
                or 0
            )
            coverage = float(section.get("term_coverage") or 0)
            rank = (new_key_count, coverage, score)

            if rank > best_rank:
                best_rank = rank
                best_index = index

        if best_rank[0] <= 0 and selected:
            break

        section = remaining.pop(best_index)
        selected.append(section)
        covered_keys.update(
            section_coverage_keys(
                question=question,
                section=section,
            )
        )

    return selected or sections[:limit]


def is_document_overview_question(question: str) -> bool:
    """Detecte une demande de resume global ou presentation du document."""
    normalized_question = normalize_for_search(question)

    return any(
        term in normalized_question
        for term in OVERVIEW_TERMS
    )


def section_order_key(section: dict[str, Any]) -> tuple[str, int, int, int]:
    """Construit une cle de tri stable selon fichier, page et index."""
    return (
        str(section.get("source") or ""),
        int(section.get("page") or 0),
        int(section.get("section_index") or 0),
        int(section.get("chunk_index") or 0),
    )


def section_text_for_overview(section: dict[str, Any]) -> str:
    """Retourne le texte combine utilise pour classer une section overview."""
    return normalize_for_search(
        " ".join(
            [
                section.get("heading") or "",
                section.get("text") or "",
            ]
        )
    )


def add_unique_section(
    selected: list[dict[str, Any]],
    seen: set[tuple[Any, Any, Any, Any]],
    section: dict[str, Any],
) -> None:
    """Ajoute une section seulement si sa localisation n'a pas deja ete prise."""
    identity = (
        section.get("source"),
        section.get("page"),
        section.get("section_index"),
        section.get("chunk_index"),
    )

    if identity in seen:
        return

    seen.add(identity)
    selected.append(section)


def document_overview_sections(
    sections: list[dict[str, Any]],
    limit: int = MAX_OVERVIEW_SECTIONS,
) -> list[dict[str, Any]]:
    """Selectionne debut, sommaire, chapitres et extraits techniques."""
    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    sorted_sections = sorted(sections, key=section_order_key)

    first_sections = [
        section
        for section in sorted_sections
        if (
            (section.get("page") is not None and section.get("page") <= 5)
            or int(section.get("section_index") or 0) <= 5
        )
    ]
    toc_sections = [
        section
        for section in sorted_sections
        if any(
            term in section_text_for_overview(section)
            for term in TABLE_OF_CONTENTS_TERMS
        )
    ]
    heading_sections = [
        section
        for section in sorted_sections
        if re.search(
            r"\b(chapter|chapitre|section)\s+\d+|\b\d+\.\d+\b",
            section_text_for_overview(section),
        )
    ]
    technical_sections = [
        section
        for section in sorted_sections
        if any(
            term in section_text_for_overview(section)
            for term in TECHNICAL_OVERVIEW_TERMS
        )
        and not any(
            term in section_text_for_overview(section)
            for term in OVERVIEW_REJECT_CODE_TERMS
        )
    ]

    for bucket, bucket_limit in (
        (first_sections, 4),
        (toc_sections, 4),
        (heading_sections, 4),
        (spread_sections(technical_sections, 5), 5),
    ):
        for section in bucket[:bucket_limit]:
            add_unique_section(selected, seen, section)

            if len(selected) >= limit:
                return selected

    for section in sorted_sections:
        add_unique_section(selected, seen, section)

        if len(selected) >= limit:
            break

    return selected


def spread_sections(
    sections: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Prend quelques sections reparties dans tout le document."""
    if len(sections) <= limit:
        return sections

    selected = []
    last_index = len(sections) - 1

    for index in range(limit):
        position = round(index * last_index / max(limit - 1, 1))
        selected.append(sections[position])

    return selected


def score_section(
    question: str,
    terms: list[str],
    text: str,
) -> int:
    """Calcule un score lexical simple pour le fallback local."""
    searchable_text = normalize_for_search(text)
    searchable_question = normalize_for_search(question)

    score = 0

    for term in terms:
        occurrences = searchable_text.count(term)

        if occurrences:
            score += min(occurrences, 5)

    for phrase in re.findall(
        r'"([^"]+)"',
        searchable_question,
    ):
        if phrase and phrase in searchable_text:
            score += 8

    return score


def compact_text(
    text: str,
    max_characters: int = 1_800,
) -> str:
    """Raccourcit un extrait long pour garder un prompt lisible."""
    cleaned = re.sub(r"\s+", " ", text).strip()

    if len(cleaned) <= max_characters:
        return cleaned

    return f"{cleaned[:max_characters].rstrip()}..."


def parse_ai_json(content: str) -> dict[str, Any]:
    """Parse la reponse JSON du modele avec un fallback texte."""
    try:
        parsed = json.loads(content)

        if isinstance(parsed, dict):
            return unwrap_nested_agent_payload(parsed)

        if isinstance(parsed, str):
            return parse_ai_json(parsed)
    except json.JSONDecodeError:
        pass

    match = re.search(
        r"\{.*\}",
        content,
        flags=re.DOTALL,
    )

    if match:
        try:
            parsed = json.loads(match.group(0))

            if isinstance(parsed, dict):
                return unwrap_nested_agent_payload(parsed)

            if isinstance(parsed, str):
                return parse_ai_json(parsed)
        except json.JSONDecodeError:
            pass

    return {
        "summary": content,
        "story": [],
        "issues": [],
        "recommendations": [],
        "references": [],
    }


def unwrap_nested_agent_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Recupere le JSON que le modele place parfois dans summary."""
    summary = payload.get("summary")

    if isinstance(summary, dict):
        nested = summary
    elif isinstance(summary, str) and summary.strip().startswith("{"):
        nested = parse_ai_json(summary)
    else:
        nested = None

    if isinstance(nested, dict) and (
        "summary" in nested
        or "sections" in nested
        or "references" in nested
    ):
        metadata = {
            key: value
            for key, value in payload.items()
            if str(key).startswith("_")
        }
        return {
            **payload,
            **nested,
            **metadata,
        }

    return payload


def unwrap_repeated_nested_agent_payload(
    payload: dict[str, Any],
    max_depth: int = 3,
) -> dict[str, Any]:
    """Applique le deballage plusieurs fois pour eviter le JSON doublement encode."""

    current = payload

    for _ in range(max_depth):
        unwrapped = unwrap_nested_agent_payload(current)

        if unwrapped == current:
            return unwrapped

        current = unwrapped

    return current


def should_repair_atomic_story(
    payload: dict[str, Any],
) -> bool:
    """Detecte les details qui melangent plusieurs faits ou sources."""
    story = payload.get("story", [])

    if not isinstance(story, list):
        return False

    for item in story:
        text = sanitize_generated_text(item)
        page_mentions = re.findall(
            r"\bp\.\s*\d+|\bpage\s+\d+",
            text,
            flags=re.IGNORECASE,
        )

        if len(set(page_mentions)) > 1:
            return True

        if re.search(
            r"\b(et|ou|and|or)\b.+\b(source|p\.|page)\b",
            text,
            flags=re.IGNORECASE,
        ) and len(text) > 180:
            return True

    return False


async def repair_atomic_response(
    payload: dict[str, Any],
    question: str,
    context: str,
) -> dict[str, Any]:
    """Demande au modele de separer les details en faits sources."""
    if not should_repair_atomic_story(payload):
        return payload

    repair_messages = [
        {
            "role": "system",
            "content": (
                "You repair a documentation-agent JSON answer. Use only "
                "the provided extracts and the draft JSON. Return only valid "
                f"JSON with this schema: {DOCUMENTATION_RESPONSE_SCHEMA}. "
                "Rewrite the answer as synthesized sections, not raw excerpts. "
                "Each section must contain one coherent technical explanation "
                "supported by source_ids. If a draft item combines unrelated "
                "contexts, cases, conditions, requests/responses, or pages, "
                "keep only the part needed to answer the question. Do not add "
                "new facts."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                f"Documentation extracts:\n{context}\n\n"
                f"Draft JSON:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]

    try:
        content = await call_hps_ai(
            repair_messages,
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )
    except (HpsAiConfigurationError, HpsAiRequestError):
        return payload

    repaired_payload = parse_ai_json(content)

    if not repaired_payload.get("summary"):
        repaired_payload["summary"] = payload.get("summary", "")

    return repaired_payload


def story_contains_source_only_items(
    payload: dict[str, Any],
    selected_sections: list[dict[str, Any]],
) -> bool:
    """Detecte avant affichage les details qui citent seulement une source."""
    story = payload.get("story", [])

    if not isinstance(story, list):
        return False

    for item in story:
        text = replace_source_id_labels(
            normalize_story_item(item),
            selected_sections,
        )

        if is_source_only_story_item(text):
            return True

    return False


async def repair_factful_response(
    payload: dict[str, Any],
    question: str,
    context: str,
) -> dict[str, Any]:
    """Regenere les details quand le modele a renvoye seulement des sources."""
    repair_messages = [
        {
            "role": "system",
            "content": (
                "You repair a documentation-agent JSON answer. Use only the "
                "provided documentation extracts. Return only valid JSON with "
                f"this schema: {DOCUMENTATION_RESPONSE_SCHEMA}. Rewrite the "
                "summary and sections so they answer the user question with "
                "concise synthesized technical explanations. Do not paste raw "
                "PDF paragraphs, OCR fragments, table rows, or source-only "
                "items. Each section must cite source_ids from the extracts. "
                "If a requested part is not present in the extracts, say 'not "
                "specified in the provided extracts'."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                f"Documentation extracts:\n{context}\n\n"
                f"Draft JSON:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]

    try:
        content = await call_hps_ai(
            repair_messages,
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )
    except (HpsAiConfigurationError, HpsAiRequestError):
        return payload

    repaired_payload = parse_ai_json(content)

    if not repaired_payload.get("summary"):
        repaired_payload["summary"] = payload.get("summary", "")

    return repaired_payload


async def repair_field_response(
    payload: dict[str, Any],
    question: str,
    context: str,
) -> dict[str, Any]:
    """Reformule une reponse de champ sous forme de fiche technique."""
    repair_messages = [
        {
            "role": "system",
            "content": (
                "You repair a documentation-agent JSON answer for a technical "
                "field. Use only the provided extracts. Return only valid JSON "
                f"with this schema: {DOCUMENTATION_RESPONSE_SCHEMA}. The "
                "summary must be a clear business answer in the user's "
                "language. The sections array must be a field card with "
                "consultant-style explanations, not copied paragraphs. Use "
                "these titles when supported by the extracts: Role, Format, "
                "Structure, Utilisation, Exemple, Controles ou rejets. Keep "
                "every section focused on the requested field. If a title is "
                "not supported by the extracts, omit it or say it is not "
                "specified. Every section must cite source_ids. Do not mention "
                "unrelated fields unless the extract explicitly links them to "
                "the requested field."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                f"Documentation extracts:\n{context}\n\n"
                f"Draft JSON:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]

    try:
        content = await call_hps_ai(
            repair_messages,
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )
    except (HpsAiConfigurationError, HpsAiRequestError):
        return payload

    repaired_payload = parse_ai_json(content)

    if not repaired_payload.get("summary"):
        repaired_payload["summary"] = payload.get("summary", "")

    return repaired_payload


def nullable_int(value: Any) -> int | None:
    """Convertit une metadonnee numerique en entier si possible."""
    if value is None:
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())

    return None


def normalize_severity(value: Any) -> str:
    """Force la severite dans les valeurs supportees par le frontend."""
    if value in {"info", "warning", "error"}:
        return str(value)

    return "info"


def sanitize_generated_text(value: Any) -> str:
    """Nettoie les placeholders et les espaces du texte genere."""
    text = str(value or "").strip()
    replacements = {
        "role from source": UNSPECIFIED_DETAIL,
        "rule from source": UNSPECIFIED_DETAIL,
        "from source": UNSPECIFIED_DETAIL,
        "exact label": "",
    }

    for placeholder, replacement in replacements.items():
        text = re.sub(
            placeholder,
            replacement,
            text,
            flags=re.IGNORECASE,
        )

    return re.sub(r"\s+", " ", text).strip(" :-")


def display_source_name(source: Any) -> str:
    """Retourne le nom metier d'un document quand il est connu."""
    raw_source = str(source or "Document")
    normalized_source = normalize_for_search(raw_source)

    return DOCUMENT_DISPLAY_NAMES.get(normalized_source, raw_source)


def normalize_reference(
    reference: dict[str, Any],
) -> dict[str, Any]:
    """Normalise une reference retournee par le modele."""
    pdf_page = nullable_int(
        reference.get("pdf_page")
        if reference.get("pdf_page") is not None
        else reference.get("page")
    )

    source = str(reference.get("source") or "Document")

    return {
        "source": display_source_name(source),
        "original_source": source,
        "page": pdf_page,
        "pdf_page": pdf_page,
        "printed_page": (
            reference.get("printed_page")
            or reference.get("page_document")
        ),
        "source_id": reference.get("source_id"),
        "section": reference.get("section"),
        "heading": reference.get("heading"),
        "sheet": reference.get("sheet"),
        "paragraph": nullable_int(reference.get("paragraph")),
    }


def format_reference_label(
    reference: dict[str, Any] | None,
) -> str | None:
    """Construit un libelle court source/page lisible."""
    if not reference:
        return None

    parts = [str(reference.get("source") or "Document")]

    if reference.get("page") is not None:
        parts.append(f"p.{reference['page']}")

    if reference.get("printed_page"):
        parts.append(f"printed {reference['printed_page']}")

    if reference.get("sheet"):
        parts.append(str(reference["sheet"]))

    if reference.get("paragraph") is not None:
        parts.append(f"para. {reference['paragraph']}")

    return " - ".join(parts)


def reference_matches_section(
    reference: dict[str, Any],
    section: dict[str, Any],
) -> bool:
    """Verifie qu'une reference du modele pointe vers une section choisie."""
    allowed_sources = {
        section.get("source"),
        display_source_name(section.get("source")),
    }

    if (
        reference.get("source") not in allowed_sources
        and reference.get("original_source") not in allowed_sources
    ):
        return False

    for key in ("page", "sheet", "paragraph"):
        if reference.get(key) is not None and reference.get(key) != section.get(key):
            return False

    return True


def reference_from_section(
    section: dict[str, Any],
) -> dict[str, Any]:
    """Cree une reference publique depuis une section stockee."""
    return {
        "source": display_source_name(section["source"]),
        "original_source": section["source"],
        "document_id": str(section.get("document_id") or ""),
        "page": section.get("page"),
        "pdf_page": section.get("page"),
        "printed_page": section.get("page_document"),
        "source_id": section.get("source_id"),
        "section": section.get("heading"),
        "heading": section.get("heading"),
        "sheet": section.get("sheet"),
        "paragraph": section.get("paragraph"),
    }


    
def evidence_from_section(
    section: dict[str, Any],
    question: str,
) -> dict[str, Any]:
    """Cree une preuve avec un court extrait pertinent."""
    return {
        **reference_from_section(section),
        "heading": section.get("heading"),
        "excerpt": extract_relevant_excerpt(
            question=question,
            text=section.get("text", ""),
        ),
        "score": section.get("rerank_score")
        or section.get("retrieval_score"),
    }


def build_evidence(
    sections: list[dict[str, Any]],
    question: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Construit une liste de preuves sans doublons."""
    evidence = []
    seen = set()

    for section in sections:
        item = evidence_from_section(
            section=section,
            question=question,
        )
        identity = (
            item.get("source"),
            item.get("page"),
            item.get("sheet"),
            item.get("paragraph"),
            item.get("excerpt"),
        )

        if identity in seen or not item.get("excerpt"):
            continue

        seen.add(identity)
        evidence.append(item)

        if len(evidence) >= limit:
            break

    return evidence


def reference_identity(
    reference: dict[str, Any],
) -> tuple[Any, Any, Any, Any]:
    """Retourne l'identite stable utilisee pour dedupliquer une reference."""
    return (
        reference.get("source"),
        reference.get("page"),
        reference.get("sheet"),
        reference.get("paragraph"),
    )


def filter_evidence_by_references(
    evidence: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Garde seulement les preuves dont la source est citee."""
    if not evidence or not references:
        return []

    reference_identities = {
        reference_identity(reference)
        for reference in references
    }

    return [
        item
        for item in evidence
        if reference_identity(item) in reference_identities
    ]


def dedupe_references(
    references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Supprime les doublons source/page/sheet/paragraphe."""
    deduped = []
    seen = set()

    for reference in references:
        identity = (
            reference.get("source"),
            reference.get("page"),
            reference.get("sheet"),
            reference.get("paragraph"),
        )

        if identity in seen:
            continue

        seen.add(identity)
        deduped.append(reference)

    return deduped


def story_item_has_source_label(
    text: str,
) -> bool:
    """Verifie si une ligne de detail cite deja une source/page."""
    return bool(
        re.search(
            r"\bsource\s*:|\bp\.\s*\d+|\bpage\s+\d+",
            text,
            flags=re.IGNORECASE,
        )
    )


def cited_references_from_story(
    story_items: list[str],
    selected_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduit les references finales depuis les sources citees en details."""
    cited_references = []

    for section in selected_sections:
        reference = reference_from_section(section)
        source = re.escape(str(reference.get("source") or ""))
        page = reference.get("page")
        sheet = reference.get("sheet")
        paragraph = reference.get("paragraph")

        for item in story_items:
            source_matches = bool(source and re.search(source, item))
            page_matches = (
                page is not None
                and re.search(
                    rf"\bp\.\s*{page}\b|\bpage\s+{page}\b",
                    item,
                    flags=re.IGNORECASE,
                )
            )
            sheet_matches = bool(sheet and str(sheet) in item)
            paragraph_matches = (
                paragraph is not None
                and re.search(
                    rf"\bpara\.\s*{paragraph}\b|\bparagraph\s+{paragraph}\b",
                    item,
                    flags=re.IGNORECASE,
                )
            )
            has_precise_location = any(
                value is not None
                for value in (page, sheet, paragraph)
            )

            if (
                page_matches
                or sheet_matches
                or paragraph_matches
                or (source_matches and not has_precise_location)
            ):
                cited_references.append(reference)
                break

    return dedupe_references(cited_references)


def replace_source_id_labels(
    text: str,
    selected_sections: list[dict[str, Any]],
) -> str:
    """Remplace Source N par la vraie reference fichier/page."""
    cleaned_text = text

    for index, section in enumerate(selected_sections, start=1):
        label = format_reference_label(reference_from_section(section))

        if not label:
            continue

        cleaned_text = re.sub(
            rf"\bSource\s+{index}\b(?:\s*,\s*(?:page|p\.)\s*\d+)?",
            label,
            cleaned_text,
            flags=re.IGNORECASE,
        )
        cleaned_text = re.sub(
            rf"\bS{index}\b",
            label,
            cleaned_text,
            flags=re.IGNORECASE,
        )

    cleaned_text = re.sub(
        r"(?:\bSource\s*:\s*){2,}",
        "Source: ",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    cleaned_text = re.sub(
        r"(\s-\sp\.(\d+)),\s*(?:page|p\.)\s*\2\b",
        r"\1",
        cleaned_text,
        flags=re.IGNORECASE,
    )

    return cleaned_text


def is_source_only_story_item(
    text: str,
) -> bool:
    """Detecte un detail qui ne contient qu'une source sans fait metier."""
    normalized_text = normalize_for_search(text)

    if not normalized_text.startswith("source"):
        return False

    factual_terms = (
        "champ",
        "code",
        "contains",
        "contient",
        "doit",
        "field",
        "fixed",
        "message",
        "presence",
        "request",
        "requete",
        "response",
        "reponse",
        "role",
        "usage",
        "used",
        "utilise",
        "valeur",
        "value",
    )

    return not any(term in normalized_text for term in factual_terms)


def fallback_story_from_sections(
    question: str,
    selected_sections: list[dict[str, Any]],
    limit: int = 4,
) -> list[str]:
    """Reconstruit des details courts si le modele ne fournit aucun detail."""
    story = []
    seen = set()

    for section in selected_sections[:limit]:
        excerpt = (
            section.get("evidence_excerpt")
            or extract_relevant_excerpt(
                question=question,
                text=section.get("text", ""),
            )
            or section.get("text", "")
        )
        excerpt = sanitize_generated_text(str(excerpt))
        sentences = re.split(r"(?<=[.!?])\s+", excerpt)
        excerpt = next(
            (
                sentence.strip()
                for sentence in sentences
                if section_coverage_keys(question, {"text": sentence})
            ),
            excerpt,
        )
        excerpt = compact_text(excerpt, max_characters=280)

        if not excerpt:
            continue

        reference_label = format_reference_label(reference_from_section(section))
        item = (
            f"{excerpt}. Source: {reference_label}"
            if reference_label and not story_item_has_source_label(excerpt)
            else excerpt
        )
        item = replace_source_id_labels(item, selected_sections)

        if (
            is_source_only_story_item(item)
            or not story_item_matches_requested_fields(item, question)
            or item in seen
        ):
            continue

        seen.add(item)
        story.append(item)

    return story


def ensure_story_has_content(
    story: list[str],
    selected_sections: list[dict[str, Any]],
    question: str,
) -> list[str]:
    """Garde seulement les details utiles et ajoute un fallback extractif."""
    useful_story = []
    seen = set()

    for item in story:
        item = sanitize_generated_text(item)

        if (
            not item
            or is_source_only_story_item(item)
            or not story_item_matches_requested_fields(item, question)
            or item in seen
        ):
            continue

        seen.add(item)
        useful_story.append(item)

    if useful_story:
        return useful_story

    return fallback_story_from_sections(
        question=question,
        selected_sections=selected_sections,
    )


def story_item_matches_requested_fields(
    text: str,
    question: str,
) -> bool:
    """Evite les details centres sur un autre champ que celui demande."""
    requested_fields = {
        normalize_field_number(field_focus)
        for field_focus in extract_field_focuses(question)
    }

    if not requested_fields:
        return True

    normalized_text = normalize_for_search(text)
    mentioned_fields = {
        normalize_field_number(match)
        for match in re.findall(
            r"\bfield\s*\d+(?:\.\d+)?\b|\bfld\s*\(?\d+(?:\.\d+)?\)?",
            normalized_text,
        )
    }

    if not mentioned_fields:
        return True

    return bool(requested_fields & mentioned_fields)


def normalize_answer_sections(
    payload: dict[str, Any],
    selected_sections: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    """Normalise les sections redigees renvoyees par le modele."""
    raw_sections = payload.get("sections")
    sections = []

    if isinstance(raw_sections, list):
        for item in raw_sections:
            normalized_section = normalize_answer_section(
                item=item,
                selected_sections=selected_sections,
                question=question,
            )

            if normalized_section:
                sections.append(normalized_section)

    if sections:
        return sections

    return sections_from_legacy_story(
        story_items=payload.get("story", []),
        selected_sections=selected_sections,
        question=question,
    )


def normalize_answer_section(
    item: Any,
    selected_sections: list[dict[str, Any]],
    question: str,
) -> dict[str, Any] | None:
    """Convertit une section du modele en bloc affichable."""
    if isinstance(item, dict):
        title = sanitize_generated_text(item.get("title") or "Explication")
        content = sanitize_generated_text(item.get("content") or "")
        paragraphs = normalize_section_paragraphs(item.get("paragraphs"))
        structured_items = normalize_section_items(item.get("items"))
        blocks = normalize_section_blocks(item.get("blocks"))
        source_ids = item.get("source_ids") or item.get("sources") or []
    else:
        text = sanitize_generated_text(item)
        title, content = split_section_text(text)
        paragraphs = []
        structured_items = []
        blocks = []
        source_ids = []

    if not blocks:
        blocks = blocks_from_legacy_section(
            content=content,
            paragraphs=paragraphs,
            items=structured_items,
        )

    blocks = replace_source_labels_in_blocks(
        blocks=blocks,
        selected_sections=selected_sections,
    )

    if paragraphs:
        paragraphs = [
            replace_source_id_labels(paragraph, selected_sections)
            for paragraph in paragraphs
        ]

    if structured_items:
        structured_items = [
            {
                "label": structured_item["label"],
                "content": replace_source_id_labels(
                    structured_item["content"],
                    selected_sections,
                ),
            }
            for structured_item in structured_items
        ]

    content = replace_source_id_labels(content, selected_sections)
    searchable_content = " ".join([
        content,
        *paragraphs,
        *[
            f"{structured_item['label']} {structured_item['content']}"
            for structured_item in structured_items
        ],
        *block_search_texts(blocks),
    ]).strip()

    if (
        not searchable_content
        or is_source_only_story_item(searchable_content)
        or not story_item_matches_requested_fields(searchable_content, question)
    ):
        return None

    if not content:
        content = " ".join(paragraphs).strip()

    return {
        "title": title or "Explication",
        "content": content,
        "paragraphs": paragraphs,
        "items": structured_items,
        "blocks": blocks,
        "source_ids": [
            str(source_id)
            for source_id in source_ids
            if source_id
        ] if isinstance(source_ids, list) else [],
    }


def normalize_section_paragraphs(value: Any) -> list[str]:
    """Nettoie les paragraphes riches generes par le modele."""
    if not isinstance(value, list):
        return []

    paragraphs = []

    for paragraph in value:
        text = sanitize_generated_text(paragraph)

        if text and not is_source_only_story_item(text):
            paragraphs.append(text)

    return paragraphs


def normalize_section_items(value: Any) -> list[dict[str, str]]:
    """Nettoie les items structures generes pour une section."""
    if not isinstance(value, list):
        return []

    items = []

    for item in value:
        if not isinstance(item, dict):
            continue

        label = sanitize_generated_text(item.get("label") or "")
        content = sanitize_generated_text(item.get("content") or "")

        if label and content:
            items.append({
                "label": label,
                "content": content,
            })

    return items


def normalize_section_blocks(value: Any) -> list[dict[str, Any]]:
    """Nettoie les blocs dynamiques generes par le modele."""
    if not isinstance(value, list):
        return []

    blocks = []

    for block in value:
        if not isinstance(block, dict):
            continue

        block_type = sanitize_generated_text(block.get("type") or "").lower()

        if block_type == "paragraph":
            content = sanitize_generated_text(block.get("content") or "")

            if content:
                blocks.append({
                    "type": "paragraph",
                    "content": content,
                })

        elif block_type == "list":
            style = sanitize_generated_text(block.get("style") or "bullet").lower()
            items = [
                sanitize_generated_text(item)
                for item in block.get("items") or []
                if sanitize_generated_text(item)
            ]

            if items:
                blocks.append({
                    "type": "list",
                    "style": style if style in {"bullet", "numbered"} else "bullet",
                    "items": items,
                })

        elif block_type == "table":
            table_content = (
                block.get("content")
                if isinstance(block.get("content"), dict)
                else {}
            )
            columns = normalize_table_columns(
                block.get("columns")
                or table_content.get("columns")
                or table_content.get("header")
                or table_content.get("headers")
            )
            rows = normalize_table_rows(
                block.get("rows") or table_content.get("rows"),
                columns,
            )

            if columns and rows:
                table_block = {
                    "type": "table",
                    "columns": columns[:8],
                    "rows": rows[:100],
                }
                title = sanitize_generated_text(block.get("title") or "")

                if title:
                    table_block["title"] = title

                blocks.append(table_block)

        elif block_type == "code":
            content = sanitize_generated_text(block.get("content") or "")

            if content:
                blocks.append({
                    "type": "code",
                    "language": sanitize_generated_text(
                        block.get("language") or "text"
                    ) or "text",
                    "content": content,
                })

        elif block_type == "key_value":
            items = []

            for item in block.get("items") or []:
                if not isinstance(item, dict):
                    continue

                label = sanitize_generated_text(item.get("label") or "")
                value_text = sanitize_generated_text(item.get("value") or "")

                if label and value_text:
                    items.append({
                        "label": label,
                        "value": value_text,
                    })

            if items:
                blocks.append({
                    "type": "key_value",
                    "items": items,
                })

        elif block_type == "callout":
            content = sanitize_generated_text(block.get("content") or "")
            severity = sanitize_generated_text(block.get("severity") or "info").lower()

            if content:
                callout = {
                    "type": "callout",
                    "severity": (
                        severity
                        if severity in {"info", "warning", "error", "success"}
                        else "info"
                    ),
                    "content": content,
                }
                title = sanitize_generated_text(block.get("title") or "")

                if title:
                    callout["title"] = title

                blocks.append(callout)

    return blocks


def normalize_table_columns(value: Any) -> list[dict[str, str]]:
    """Nettoie les colonnes d'un tableau genere."""
    if not isinstance(value, list):
        return []

    columns = []
    seen = set()

    for index, column in enumerate(value):
        if isinstance(column, dict):
            key = sanitize_generated_text(column.get("key") or "")
            label = sanitize_generated_text(column.get("label") or "")
        else:
            label = sanitize_generated_text(column)
            key = re.sub(r"[^a-zA-Z0-9_]+", "_", label.lower()).strip("_")
            key = key or f"column_{index + 1}"

        if key and label and key not in seen:
            seen.add(key)
            columns.append({
                "key": key,
                "label": label,
            })

    return columns


def normalize_table_rows(
    value: Any,
    columns: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Nettoie les lignes d'un tableau genere."""
    if not isinstance(value, list):
        return []

    rows = []
    keys = [column["key"] for column in columns]
    seen = set()

    for row in value:
        if isinstance(row, dict):
            normalized_row = {
                key: sanitize_generated_text(row.get(key) or "")
                for key in keys
            }
        elif isinstance(row, list):
            normalized_row = {
                key: sanitize_generated_text(row[index] if index < len(row) else "")
                for index, key in enumerate(keys)
            }
        else:
            continue


        if not any(normalized_row.values()):
            continue

        signature = tuple(normalized_row.get(key, "") for key in keys)

        if signature in seen:
            continue

        seen.add(signature)
        rows.append(normalized_row)

    return rows


def blocks_from_legacy_section(
    content: str,
    paragraphs: list[str],
    items: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Convertit l'ancien format en blocs dynamiques."""
    blocks = []

    for paragraph in paragraphs or ([content] if content else []):
        if paragraph:
            blocks.append({
                "type": "paragraph",
                "content": paragraph,
            })

    if items:
        blocks.append({
            "type": "key_value",
            "items": [
                {
                    "label": item["label"],
                    "value": item["content"],
                }
                for item in items
            ],
        })

    return blocks


def replace_source_labels_in_blocks(
    blocks: list[dict[str, Any]],
    selected_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remplace les labels source_ids dans les blocs affichables."""
    normalized_blocks = []

    for block in blocks:
        block_type = block.get("type")

        if block_type in {"paragraph", "code", "callout"}:
            normalized_block = {
                **block,
                "content": replace_source_id_labels(
                    block.get("content", ""),
                    selected_sections,
                ),
            }

        elif block_type == "list":
            normalized_block = {
                **block,
                "items": [
                    replace_source_id_labels(item, selected_sections)
                    for item in block.get("items") or []
                ],
            }

        elif block_type == "key_value":
            normalized_block = {
                **block,
                "items": [
                    {
                        **item,
                        "value": replace_source_id_labels(
                            item.get("value", ""),
                            selected_sections,
                        ),
                    }
                    for item in block.get("items") or []
                ],
            }

        elif block_type == "table":
            normalized_block = {
                **block,
                "rows": [
                    {
                        key: replace_source_id_labels(value, selected_sections)
                        for key, value in row.items()
                    }
                    for row in block.get("rows") or []
                ],
            }

        else:
            normalized_block = block

        normalized_blocks.append(normalized_block)

    return normalized_blocks


def block_search_texts(blocks: list[dict[str, Any]]) -> list[str]:
    """Transforme les blocs en texte pour les filtres de pertinence."""
    texts = []

    for block in blocks:
        block_type = block.get("type")

        if block_type in {"paragraph", "code", "callout"}:
            texts.append(block.get("content", ""))
        elif block_type == "list":
            texts.extend(block.get("items") or [])
        elif block_type == "key_value":
            texts.extend(
                f"{item.get('label', '')} {item.get('value', '')}"
                for item in block.get("items") or []
            )
        elif block_type == "table":
            for row in block.get("rows") or []:
                texts.extend(row.values())

    return [
        sanitize_generated_text(text)
        for text in texts
        if sanitize_generated_text(text)
    ]


def split_section_text(text: str) -> tuple[str, str]:
    """Deduit un titre de section depuis une ancienne ligne de detail."""
    match = re.match(
        r"^(?:field\s*\d+(?:\.\d+)?\s*[-:]\s*)?"
        r"(role|format(?:/length)?|structure|usage|utilisation|example|"
        r"exemple|values?|valeurs?|reject codes?|controles?|exact pages?)"
        r"\s*[:\-]\s*(.+)$",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return "Explication", text

    title = match.group(1).strip()
    title_map = {
        "role": "Role",
        "format": "Format",
        "format/length": "Format",
        "structure": "Structure",
        "usage": "Utilisation",
        "utilisation": "Utilisation",
        "example": "Exemple",
        "exemple": "Exemple",
        "value": "Valeurs",
        "values": "Valeurs",
        "valeur": "Valeurs",
        "valeurs": "Valeurs",
        "reject code": "Controles ou rejets",
        "reject codes": "Controles ou rejets",
        "controles": "Controles ou rejets",
        "exact pages": "References",
    }

    return title_map.get(normalize_for_search(title), title), match.group(2)


def sections_from_legacy_story(
    story_items: Any,
    selected_sections: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    """Transforme l'ancien schema story en sections non numerotees."""
    if not isinstance(story_items, list):
        return []

    sections = []

    for item in story_items:
        text = replace_source_id_labels(
            normalize_story_item(item),
            selected_sections,
        )

        if (
            not text
            or is_source_only_story_item(text)
            or not story_item_matches_requested_fields(text, question)
        ):
            continue

        title, content = split_section_text(text)
        sections.append({
            "title": title,
            "content": content,
            "paragraphs": [content] if content else [],
            "items": [],
            "blocks": blocks_from_legacy_section(
                content=content,
                paragraphs=[content] if content else [],
                items=[],
            ),
            "source_ids": [],
        })

    return sections


def references_from_section_source_ids(
    answer_sections: list[dict[str, Any]],
    selected_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Construit les references depuis les source_ids cites par section."""
    source_ids = []

    for answer_section in answer_sections:
        for source_id in answer_section.get("source_ids", []):
            source_ids.append(str(source_id))

    if not source_ids:
        return []

    source_id_set = set(source_ids)

    return dedupe_references([
        reference_from_section(section)
        for section in selected_sections
        if str(section.get("source_id")) in source_id_set
    ])


def normalize_agent_response(
    payload: dict[str, Any],
    selected_sections: list[dict[str, Any]],
    enforce_story_sources: bool = False,
    evidence: list[dict[str, Any]] | None = None,
    question: str = "",
) -> dict[str, Any]:
    """Normalise la reponse du modele vers le schema attendu par le frontend."""
    payload = unwrap_repeated_nested_agent_payload(payload)
    references = payload.get("references")
    answer_sections = normalize_answer_sections(
        payload=payload,
        selected_sections=selected_sections,
        question=question,
    )

    if isinstance(references, list):
        normalized_payload_references = []

        for reference in references:
            if not isinstance(reference, dict):
                continue

            source_id = reference.get("source_id")
            source_section = next(
                (
                    section
                    for section in selected_sections
                    if source_id and section.get("source_id") == source_id
                ),
                None,
            )

            if source_section:
                normalized_payload_references.append(
                    reference_from_section(source_section)
                )
            else:
                normalized_payload_references.append(
                    normalize_reference(reference)
                )

        references = [
            reference
            for reference in normalized_payload_references
            if any(
                reference_matches_section(reference, section)
                for section in selected_sections
            )
        ]

    if not isinstance(references, list) or not references:
        references = [
            reference_from_section(section)
            for section in selected_sections[:4]
        ]

    default_reference = (
        reference_from_section(selected_sections[0])
        if enforce_story_sources and selected_sections
        else None
    )
    story = []

    if enforce_story_sources:
        section_references = references_from_section_source_ids(
            answer_sections=answer_sections,
            selected_sections=selected_sections,
        )
        cited_references = section_references
        references = (
            cited_references
            if cited_references
            else references[:1]
        )

    references = dedupe_references(references)
    evidence = filter_evidence_by_references(
        evidence=evidence or [],
        references=references,
    )

    return {
        "summary": sanitize_generated_text(payload.get("summary")),
        "sections": answer_sections,
        "story": story,
        "issues": [
            {
                "severity": normalize_severity(
                    issue.get("severity")
                    if isinstance(issue, dict)
                    else None
                ),
                "title": issue.get("title", "")
                if isinstance(issue, dict)
                else str(issue),
                "detail": sanitize_generated_text(issue.get("detail"))
                if isinstance(issue, dict)
                else None,
            }
            for issue in payload.get("issues", [])
            if issue
        ],
        "recommendations": [
            sanitize_generated_text(item)
            for item in payload.get("recommendations", [])
            if item
        ],
        "references": references,
        "evidence": evidence,
    }


def normalize_story_item(
    item: Any,
    default_reference: dict[str, Any] | None = None,
) -> str:
    """Convertit un detail en phrase claire avec source."""
    if not isinstance(item, dict):
        text = sanitize_generated_text(item)

        if default_reference and not story_item_has_source_label(text):
            reference_label = format_reference_label(default_reference)

            if reference_label:
                return f"{text}. Source: {reference_label}"

        return text

    field = item.get("field") or item.get("name")
    role = (
        item.get("role")
        or item.get("description")
        or item.get("explanation")
    )
    matching_rule = (
        item.get("matching_rule")
        or item.get("rule")
        or item.get("requirement")
    )
    item_reference = normalize_reference(item)
    source = (
        format_reference_label(item_reference)
        if item.get("source") or item.get("page") is not None
        else None
    )

    field = sanitize_generated_text(field) if field else None
    role = sanitize_generated_text(role) if role else None
    matching_rule = (
        sanitize_generated_text(matching_rule)
        if matching_rule
        else None
    )

    parts = []

    if field:
        parts.append(str(field))

    if role:
        if parts:
            parts[-1] = f"{parts[-1]}: {role}"
        else:
            parts.append(str(role))

    if matching_rule:
        parts.append(f"Regle: {matching_rule}")

    if source:
        parts.append(f"Source: {source}")
    elif default_reference:
        reference_label = format_reference_label(default_reference)

        if reference_label:
            parts.append(f"Source: {reference_label}")

    if parts:
        return " - ".join(parts)

    return ", ".join(
        f"{key}: {value}"
        for key, value in item.items()
        if value is not None
    )


def build_extractive_fallback_response(
    selected_sections: list[dict[str, Any]],
    error_message: str,
    question: str = "",
) -> dict[str, Any]:
    """Retourne une erreur propre sans exposer les chunks bruts."""
    references = [
        reference_from_section(section)
        for section in selected_sections[:4]
    ]
    sections = []

    if question_requests_table(question):
        rows = dedupe_code_rows(
            code_rows_from_sections(
                selected_sections,
                question,
            )
        )

        if len(rows) >= 2:
            source_map = {
                str(section.get("source_id")): format_reference_label(
                    reference_from_section(section)
                )
                for section in selected_sections
                if section.get("source_id")
            }
            display_rows = []

            for row in rows:
                reference_labels = [
                    source_map.get(source_id, source_id)
                    for source_id in re.findall(
                        r"\bS\d+\b",
                        row.get("reference", ""),
                    )
                ]
                display_rows.append({
                    **row,
                    "reference": ", ".join(
                        label
                        for label in dict.fromkeys(reference_labels)
                        if label
                    ),
                })

            sections = [
                {
                    "title": "Valeurs et significations",
                    "content": "",
                    "paragraphs": [
                        (
                            "Le tableau ci-dessous est extrait "
                            "deterministiquement des sources documentaires "
                            "retrouvees. Le service de generation n'etait pas "
                            "disponible pour rediger une synthese complete."
                        )
                    ],
                    "items": [],
                    "blocks": [
                        {
                            "type": "paragraph",
                            "content": (
                                "Le tableau ci-dessous est extrait "
                                "deterministiquement des sources documentaires "
                                "retrouvees."
                            ),
                        },
                        {
                            "type": "table",
                            "columns": [
                                {"key": "code", "label": "Code"},
                                {
                                    "key": "meaning",
                                    "label": "Signification",
                                },
                                {
                                    "key": "reference",
                                    "label": "Reference",
                                },
                            ],
                            "rows": display_rows,
                        },
                    ],
                    "source_ids": source_ids_from_rows(rows),
                }
            ]
            referenced_source_ids = set(source_ids_from_rows(rows))
            table_references = [
                reference_from_section(section)
                for section in selected_sections
                if str(section.get("source_id")) in referenced_source_ids
            ]

            if table_references:
                references = dedupe_references(table_references)

    return {
        "summary": (
            "Les sources documentaires ont ete retrouvees, mais le service "
            "de generation n'est pas disponible pour produire une synthese "
            "fiable."
        ),
        "sections": sections,
        "story": [],
        "issues": [
            {
                "severity": "warning",
                "title": "Service de generation indisponible",
                "detail": error_message,
            }
        ],
        "recommendations": [],
        "references": references,
    }

async def load_sections(
    conversation_id: str,
    referenced_document_ids: list[str] | None = None,
    include_all_agents: bool = False,
) -> list[dict[str, Any]]:
    """Charge les chunks de documentation extraits pour une conversation."""
    object_ids = [
        ObjectId(document_id)
        for document_id in referenced_document_ids or []
        if ObjectId.is_valid(document_id)
    ]

    if object_ids:
        documents = await documents_collection.find(
            {
                "_id": {"$in": object_ids},
                "status": "extracted",
                "extension": {"$in": sorted(REFERENCE_DOCUMENT_EXTENSIONS)},
            }
        ).to_list(length=100)
    else:
        query = {
            "conversation_id": conversation_id,
            "status": "extracted",
        }

        if include_all_agents:
            query["extension"] = {"$in": sorted(REFERENCE_DOCUMENT_EXTENSIONS)}
        else:
            query["agent"] = "documentation"

        documents = await documents_collection.find(query).to_list(length=100)

    documents = await documents_collection.find(
        {
            "_id": {"$in": [document["_id"] for document in documents]},
        }
    ).sort("created_at", 1).to_list(length=100)

    if not documents:
        return []

    documents_by_id = {
        str(document["_id"]): document
        for document in documents
    }

    cursor = document_sections_collection.find(
        {
            "document_id": {
                "$in": list(documents_by_id.keys()),
            }
        }
    ).sort(
        [
            ("document_id", 1),
            ("section_index", 1),
        ]
    )

    sections = await cursor.to_list(length=2_000)

    enriched_sections = []

    for section in sections:
        document = documents_by_id.get(section["document_id"])

        if document is None:
            continue

        enriched_sections.append({
            "document_id": section.get("document_id"),
            "source_section_id": str(section.get("_id")),
            "source": document["original_filename"],
            "text": section.get("text", ""),
            "page": section.get("page"),
            "page_document": section.get("page_document"),
            "sheet": section.get("sheet"),
            "paragraph": section.get("paragraph"),
            "heading": section.get("heading"),
            "field_number": section.get("field_number"),
            "field_name": section.get("field_name"),
            "content_type": section.get("content_type"),
            "section_index": section.get("section_index"),
            "chunk_index": section.get("chunk_index"),
            "embedding": section.get("embedding"),
        })

    return enriched_sections


def select_relevant_sections(
    question: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Selectionne lexicalement les sections en fallback historique."""
    terms = extract_terms(question)

    ranked_sections = []

    for section in sections:
        score = score_section(
            question=question,
            terms=terms,
            text=section["text"],
        )

        ranked_sections.append((score, section))

    ranked_sections.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    selected = [
        section
        for score, section in ranked_sections
        if score > 0
    ][:MAX_SELECTED_SECTIONS]

    if selected:
        return selected

    return sections[:MAX_SELECTED_SECTIONS]


def build_context(
    sections: list[dict[str, Any]],
) -> str:
    """Construit le contexte RAG a partir des sections selectionnees."""
    blocks = []
    total_length = 0

    for index, section in enumerate(sections, start=1):
        location_parts = []

        if section.get("page") is not None:
            location_parts.append(f"page {section['page']}")

        if section.get("page_document") is not None:
            location_parts.append(
                f"printed page {section['page_document']}"
            )

        if section.get("sheet") is not None:
            location_parts.append(f"sheet {section['sheet']}")

        if section.get("paragraph") is not None:
            location_parts.append(
                f"paragraph {section['paragraph']}"
            )

        if section.get("chunk_index") is not None:
            location_parts.append(
                f"chunk {section['chunk_index']}"
            )

        location = ", ".join(location_parts) or "no precise location"
        excerpt = evidence_text(section)
        evidence_excerpt = section.get("evidence_excerpt")
        heading = section.get("heading") or "No heading"
        metadata_parts = []

        if section.get("field_number"):
            metadata_parts.append(f"field_number: {section['field_number']}")

        if section.get("field_name"):
            metadata_parts.append(f"field_name: {section['field_name']}")

        if section.get("content_type"):
            metadata_parts.append(f"content_type: {section['content_type']}")

        metadata = "\n".join(metadata_parts)
        block = (
            f"[Source {index}]\n"
            f"source_id: {section.get('source_id') or f'S{index}'}\n"
            f"file: {section['source']}\n"
            f"heading: {heading}\n"
            f"location: {location}\n"
            f"{metadata}\n"
            f"text: {evidence_excerpt or excerpt}"
        )

        if total_length + len(block) > MAX_CONTEXT_CHARACTERS:
            break

        blocks.append(block)
        total_length += len(block)

    return "\n\n".join(blocks)


def evidence_text(
    section: dict[str, Any],
) -> str:
    """Prepare le texte source sans tronquer les tableaux/codes importants."""
    text = section.get("text", "")
    content_type = section.get("content_type")
    heading = normalize_for_search(section.get("heading") or "")

    if (
        content_type in {"field_values", "field_edits"}
        or "valid values" in heading
        or "reject codes" in heading
        or "field edits" in heading
        or "table" in heading
    ):
        return compact_text(text, max_characters=5_000)

    return compact_text(text, max_characters=1_800)


async def answer_documentation_question(
    question: str,
    conversation_id: str | None,
    referenced_document_ids: list[str] | None = None,
    include_all_agents: bool = False,
    force_legacy: bool = False,
) -> dict[str, Any]:
    """Pipeline principal du Documentation Agent: RAG, prompt, normalisation."""
    legacy_started = time.perf_counter()
    effective_question = question
    memory_resolution = None
    memory_state = None

    if not conversation_id:
        raise HTTPException(
            status_code=400,
            detail="A conversation_id is required for Documentation Agent.",
        )

    try:
        memory_resolution, memory_state, _recent_messages = await resolve_documentation_query(
            question=question,
            conversation_id=conversation_id,
        )
        effective_question = memory_resolution.resolved_query
    except Exception as error:
        logger.info("MEMORY_RESOLUTION_ERROR %s", error)

    if (
        memory_resolution
        and memory_resolution.query_type == "CONVERSATION_RECALL"
    ):
        if memory_resolution.recall_answer:
            await update_documentation_memory(
                conversation_id=conversation_id,
                state=memory_state,
                resolved=memory_resolution,
                referenced_document_ids=referenced_document_ids,
            )
            return {
                "summary": memory_resolution.recall_answer,
                "sections": [],
                "story": [],
                "issues": [],
                "recommendations": [],
                "references": [],
            }

        return {
            "summary": (
                "Je n'ai pas assez de contexte fiable pour identifier le "
                "sujet precedent. Peux-tu preciser le Field, le message ou "
                "le concept concerne ?"
            ),
            "sections": [],
            "story": [],
            "issues": [
                {
                    "severity": "warning",
                    "title": "Contexte conversationnel ambigu",
                    "detail": memory_resolution.ambiguity_reason
                    or "CONVERSATION_CONTEXT_AMBIGUOUS",
                }
            ],
            "recommendations": [],
            "references": [],
        }

    screenshot_response = await answer_screenshot_question(
        question=effective_question,
        conversation_id=conversation_id,
        referenced_document_ids=referenced_document_ids,
        agent="documentation",
    )

    if screenshot_response is not None:
        await update_documentation_memory(
            conversation_id=conversation_id,
            state=memory_state,
            resolved=memory_resolution,
            referenced_document_ids=referenced_document_ids,
        )
        return screenshot_response

    try:
        function_response = await answer_function_question(
            question=effective_question,
            original_question=question,
            conversation_id=conversation_id,
            referenced_document_ids=referenced_document_ids,
            memory_resolution=memory_resolution,
        )

        if function_response is not None:
            await update_documentation_memory(
                conversation_id=conversation_id,
                state=memory_state,
                resolved=memory_resolution,
                referenced_document_ids=referenced_document_ids,
            )
            return function_response
    except Exception as error:
        logger.info("FUNCTION_CATALOG_FALLBACK reason=%s", error)

    sections = await load_sections(
        conversation_id,
        referenced_document_ids=referenced_document_ids,
        include_all_agents=include_all_agents,
    )

    if not sections:
        return {
            "summary": (
                "Aucun document extrait n'est disponible pour cette "
                "conversation. Ajoute un document PDF, DOCX, XLSX, TXT, "
                "LOG ou une capture d'ecran, puis repose ta question."
            ),
            "story": [],
            "issues": [
                {
                    "severity": "warning",
                    "title": "No extracted documentation found",
                    "detail": (
                        "The Documentation Agent can only answer from "
                        "documents uploaded to the current conversation."
                    ),
                }
            ],
            "recommendations": [
                "Upload the technical document in the current conversation.",
                "Check that the document status is extracted after upload.",
            ],
            "references": [],
        }

    if (
        evidence_table_lookup_generation_enabled()
        and not force_legacy
    ):
        try:
            table_response = await try_generate_table_lookup_response(
                question=effective_question,
                sections=sections,
                original_question=question,
                conversation_id=conversation_id,
                memory_resolution=memory_resolution,
            )
            await update_documentation_memory(
                conversation_id=conversation_id,
                state=memory_state,
                resolved=memory_resolution,
                referenced_document_ids=referenced_document_ids,
            )
            return table_response
        except Exception as error:
            logger.info(
                "EVIDENCE_TABLE_LOOKUP_FALLBACK reason=%s",
                error,
            )

    if evidence_generation_enabled() and not force_legacy:
        try:
            evidence_response = await try_generate_evidence_response(
                question=effective_question,
                sections=sections,
                original_question=question,
                conversation_id=conversation_id,
                memory_resolution=memory_resolution,
            )
            await update_documentation_memory(
                conversation_id=conversation_id,
                state=memory_state,
                resolved=memory_resolution,
                referenced_document_ids=referenced_document_ids,
            )
            return evidence_response
        except Exception as error:
            logger.info(
                "EVIDENCE_PIPELINE_FALLBACK reason=%s",
                error,
            )

    retrieval_question = expand_documentation_question(effective_question)
    intent = classify_intent(effective_question)
    overview_mode = intent == DOCUMENT_OVERVIEW
    field_mode = intent in {ISO_FIELD_EXPLANATION, ISO_VALUE_DECODING}

    if overview_mode:
        selected_sections = document_overview_sections(sections)
    else:
        field_sections = exact_field_sections(
            question=retrieval_question,
            sections=sections,
        )
        if field_mode and field_sections:
            selected_sections = field_sections_with_neighbors(
                field_sections=field_sections,
                all_sections=sections,
            )[:MAX_FIELD_EVIDENCE_SECTIONS]
            selected_sections = prioritize_value_table_sections(
                question=retrieval_question,
                sections=selected_sections,
            )
        else:
            selected_sections = await retrieve_relevant_sections(
                question=retrieval_question,
                sections=sections,
                limit=MAX_SELECTED_SECTIONS,
            )

    selected_sections = [
        {
            **section,
            "source_id": f"S{index}",
            "evidence_excerpt": (
                evidence_text(section)
                if overview_mode or field_mode
                else extract_relevant_excerpt(
                    question=retrieval_question,
                    text=section.get("text", ""),
                )
            ),
        }
        for index, section in enumerate(selected_sections, start=1)
    ]

    if not overview_mode and not field_mode:
        selected_sections = compact_sections_by_query_coverage(
            question=retrieval_question,
            sections=selected_sections,
        )
    evidence = build_evidence(
        sections=selected_sections,
        question=retrieval_question,
    )

    context = build_context(selected_sections)

    try:
        payload = await KnowledgeGenerationPipeline.generate(
            question=effective_question,
            intent=intent,
            context=context,
            selected_sections=selected_sections,
        )
    except HpsAiConfigurationError as error:
        return build_extractive_fallback_response(
            selected_sections=selected_sections,
            error_message=str(error),
            question=retrieval_question,
        )
    except HpsAiRequestError as error:
        return build_extractive_fallback_response(
            selected_sections=selected_sections,
            error_message=str(error),
            question=retrieval_question,
        )

    response = normalize_agent_response(
        payload=payload,
        selected_sections=selected_sections,
        enforce_story_sources=True,
        evidence=evidence,
        question=retrieval_question,
    )

    await update_documentation_memory(
        conversation_id=conversation_id,
        state=memory_state,
        resolved=memory_resolution,
        referenced_document_ids=referenced_document_ids,
    )

    if evidence_shadow_enabled() and not force_legacy:
        shadow = await shadow_evidence_response(
            question=effective_question,
            sections=sections,
            original_question=question,
            conversation_id=conversation_id,
            memory_resolution=memory_resolution,
        )
        comparison_payload = {
            "question": question,
            "resolved_question": effective_question,
            "memory_resolution": (
                memory_resolution.model_dump()
                if memory_resolution
                else None
            ),
            "old_response": response,
            "evidence_response": shadow.get("response"),
            "intent": shadow.get("intent"),
            "answer_requirements": shadow.get("answer_requirements"),
            "retrieval_complete": shadow.get("retrieval_complete"),
            "validation": shadow.get("validation"),
            "evidence_bundle": shadow.get("evidence_bundle"),
            "latency_old_ms": round(
                (time.perf_counter() - legacy_started) * 1000
            ),
            "latency_evidence_ms": shadow.get("latency_ms"),
            "latency_evidence": shadow.get("latency"),
            "error": shadow.get("error"),
            "fallback_reason": shadow.get("fallback_reason"),
        }
        logger.info(
            "EVIDENCE_PIPELINE_SHADOW_RESULT %s",
            json.dumps(
                comparison_payload,
                ensure_ascii=False,
                default=str,
            ),
        )

    return response
