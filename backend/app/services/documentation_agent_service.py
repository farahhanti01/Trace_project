import json
import re
import unicodedata
from typing import Any

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


MAX_CONTEXT_CHARACTERS = 14_000
MAX_SELECTED_SECTIONS = 8
MAX_FIELD_FOCUSED_SECTIONS = 4
MAX_COMPACT_EVIDENCE_SECTIONS = 4
UNSPECIFIED_DETAIL = "non specifie dans les extraits fournis"

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
    normalized = unicodedata.normalize("NFKD", value.lower())
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def extract_terms(question: str) -> list[str]:
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
    return [
        re.sub(r"\s+", " ", match).strip()
        for match in dict.fromkeys(
            re.findall(
                r"\bfield\s*\d+(?:\.\d+)?\b",
                normalize_for_search(question),
            )
        )
    ]


def extract_exact_tokens(question: str) -> list[str]:
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
    section_text = normalize_for_search(
        " ".join(
            [
                section.get("heading") or "",
                section.get("text") or "",
            ]
        )
    )
    compact_section_text = section_text.replace(" ", "")
    compact_field_focus = field_focus.replace(" ", "")
    numeric_focus = field_focus.replace("field", "").strip()

    return (
        field_focus in section_text
        or compact_field_focus in compact_section_text
        or numeric_focus in section_text
    )


def focus_sections_on_exact_fields(
    question: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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


def section_coverage_keys(
    question: str,
    section: dict[str, Any],
) -> set[str]:
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


def score_section(
    question: str,
    terms: list[str],
    text: str,
) -> int:
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
    cleaned = re.sub(r"\s+", " ", text).strip()

    if len(cleaned) <= max_characters:
        return cleaned

    return f"{cleaned[:max_characters].rstrip()}..."


def parse_ai_json(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    match = re.search(
        r"\{.*\}",
        content,
        flags=re.DOTALL,
    )

    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {
        "summary": content,
        "story": [],
        "issues": [],
        "recommendations": [],
        "references": [],
    }


def should_repair_atomic_story(
    payload: dict[str, Any],
) -> bool:
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
    if not should_repair_atomic_story(payload):
        return payload

    repair_messages = [
        {
            "role": "system",
            "content": (
                "You repair a documentation-agent JSON answer. Use only "
                "the provided extracts and the draft JSON. Return only valid "
                "JSON with the same schema. Rewrite story as atomic evidence "
                "items: one item must contain one verifiable fact from one "
                "source/page. If a draft item combines several contexts, "
                "cases, conditions, requests/responses, or pages, split it "
                "into separate items or keep only the part needed to answer "
                "the question. Do not add new facts. Keep 2 to 4 story items "
                "unless the evidence requires fewer. Each story item must end "
                "with its source/page."
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
    if value is None:
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())

    return None


def normalize_severity(value: Any) -> str:
    if value in {"info", "warning", "error"}:
        return str(value)

    return "info"


def sanitize_generated_text(value: Any) -> str:
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


def normalize_reference(
    reference: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source": str(reference.get("source") or "Document"),
        "page": nullable_int(reference.get("page")),
        "sheet": reference.get("sheet"),
        "paragraph": nullable_int(reference.get("paragraph")),
    }


def format_reference_label(
    reference: dict[str, Any] | None,
) -> str | None:
    if not reference:
        return None

    parts = [str(reference.get("source") or "Document")]

    if reference.get("page") is not None:
        parts.append(f"p.{reference['page']}")

    if reference.get("sheet"):
        parts.append(str(reference["sheet"]))

    if reference.get("paragraph") is not None:
        parts.append(f"para. {reference['paragraph']}")

    return " - ".join(parts)


def reference_matches_section(
    reference: dict[str, Any],
    section: dict[str, Any],
) -> bool:
    if reference.get("source") != section.get("source"):
        return False

    for key in ("page", "sheet", "paragraph"):
        if reference.get(key) is not None and reference.get(key) != section.get(key):
            return False

    return True


def reference_from_section(
    section: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source": section["source"],
        "page": section.get("page"),
        "sheet": section.get("sheet"),
        "paragraph": section.get("paragraph"),
    }


    
def evidence_from_section(
    section: dict[str, Any],
    question: str,
) -> dict[str, Any]:
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


def normalize_agent_response(
    payload: dict[str, Any],
    selected_sections: list[dict[str, Any]],
    enforce_story_sources: bool = False,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    references = payload.get("references")

    if isinstance(references, list):
        normalized_payload_references = [
            normalize_reference(reference)
            for reference in references
            if isinstance(reference, dict)
        ]
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
    story = [
        normalize_story_item(
            item,
            default_reference=default_reference,
        )
        for item in payload.get("story", [])
        if item
    ]

    if enforce_story_sources:
        cited_references = cited_references_from_story(
            story_items=story,
            selected_sections=selected_sections,
        )
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
) -> dict[str, Any]:
    excerpts = [
        (
            f"{section['source']}"
            f"{f' - p.{section['page']}' if section.get('page') is not None else ''}"
            f"{f' - {section['sheet']}' if section.get('sheet') else ''}"
            f"{f' - para. {section['paragraph']}' if section.get('paragraph') is not None else ''}: "
            f"{compact_text(section['text'], max_characters=450)}"
        )
        for section in selected_sections[:4]
    ]

    generic_sources_only = bool(selected_sections) and all(
        front_matter_penalty(section) >= 0.55
        for section in selected_sections[:4]
    )

    summary = (
        "Le document a bien ete extrait, mais l'appel HPS/Ocean AI "
        "n'est pas disponible pour generer une reponse complete. "
        "Voici les passages les plus pertinents retrouves localement."
    )

    if generic_sources_only:
        summary = (
            "Les passages retrouves localement sont trop generaux "
            "(confidentialite, copyright, introduction ou sommaire). "
            "La question doit etre plus ciblee, ou le document doit etre "
            "reindexe avec des sections techniques plus precises."
        )

    return {
        "summary": summary,
        "story": excerpts,
        "issues": [
            {
                "severity": "warning",
                "title": "HPS/Ocean AI unavailable",
                "detail": error_message,
            }
        ],
        "recommendations": [
            "Verifier que HPS/Ocean AI est accessible depuis ce poste.",
            "Verifier la valeur exacte de HPS_AI_URL ou OCEAN_AI_BASE_URL.",
            "Relancer la question lorsque le service HPS/Ocean AI repond.",
        ],
        "references": [
            reference_from_section(section)
            for section in selected_sections[:4]
        ],
    }


async def load_sections(
    conversation_id: str,
) -> list[dict[str, Any]]:
    documents = await documents_collection.find(
        {
            "conversation_id": conversation_id,
            "agent": "documentation",
            "status": "extracted",
        }
    ).to_list(length=100)

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
            "source": document["original_filename"],
            "text": section.get("text", ""),
            "page": section.get("page"),
            "sheet": section.get("sheet"),
            "paragraph": section.get("paragraph"),
            "heading": section.get("heading"),
            "section_index": section.get("section_index"),
            "chunk_index": section.get("chunk_index"),
            "embedding": section.get("embedding"),
        })

    return enriched_sections


def select_relevant_sections(
    question: str,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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
    blocks = []
    total_length = 0

    for index, section in enumerate(sections, start=1):
        location_parts = []

        if section.get("page") is not None:
            location_parts.append(f"page {section['page']}")

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
        excerpt = compact_text(section["text"])
        evidence_excerpt = section.get("evidence_excerpt")
        heading = section.get("heading") or "No heading"
        block = (
            f"[Source {index}]\n"
            f"source_id: S{index}\n"
            f"file: {section['source']}\n"
            f"heading: {heading}\n"
            f"location: {location}\n"
            f"text: {evidence_excerpt or excerpt}"
        )

        if total_length + len(block) > MAX_CONTEXT_CHARACTERS:
            break

        blocks.append(block)
        total_length += len(block)

    return "\n\n".join(blocks)


async def answer_documentation_question(
    question: str,
    conversation_id: str | None,
) -> dict[str, Any]:
    if not conversation_id:
        raise HTTPException(
            status_code=400,
            detail="A conversation_id is required for Documentation Agent.",
        )

    sections = await load_sections(conversation_id)

    if not sections:
        return {
            "summary": (
                "Aucun document extrait n'est disponible pour cette "
                "conversation. Ajoute un document PDF, DOCX, XLSX, TXT "
                "ou LOG, puis repose ta question."
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

    selected_sections = await retrieve_relevant_sections(
        question=question,
        sections=sections,
        limit=MAX_SELECTED_SECTIONS,
    )
    selected_sections = focus_sections_on_exact_fields(
        question=question,
        sections=selected_sections,
    )
    selected_sections = [
        {
            **section,
            "evidence_excerpt": extract_relevant_excerpt(
                question=question,
                text=section.get("text", ""),
            ),
        }
        for section in selected_sections
    ]
    selected_sections = compact_sections_by_query_coverage(
        question=question,
        sections=selected_sections,
    )
    evidence = build_evidence(
        sections=selected_sections,
        question=question,
    )

    context = build_context(selected_sections)

    messages = [
        {
            "role": "system",
            "content": (
                "You are Documentation Agent, a technical documentation "
                "expert. Answer only from the provided documentation "
                "extracts. Never use prior knowledge or assumptions to fill "
                "missing details. If the extracts are insufficient, say so "
                "clearly and do not invent the missing answer. "
                "Return only valid JSON using this schema: "
                "{"
                '"summary": string, '
                '"story": string[], '
                '"issues": [{"severity": "info|warning|error", '
                '"title": string, "detail": string|null}], '
                '"recommendations": string[], '
                '"references": [{"source": string, "page": number|null, '
                '"sheet": string|null, "paragraph": number|null}]'
                "}. The summary must answer the user question directly with "
                "the strongest facts present in the extracts. It must not "
                "repeat user instructions. Every factual claim must be "
                "supported by one of the provided sources. "
                "Prefer precise technical requirements, business rules, "
                "message fields, error cases, functions, workflows, and "
                "exceptions. Do not use confidentiality, copyright, cover "
                "pages, table of contents, revision history, or generic "
                "introduction sections as answer sources unless the user "
                "explicitly asks about those topics. If the provided extracts "
                "are too generic, state that the retrieved sources are "
                "insufficient for a precise technical answer. When a source "
                "contains a table of fields, codes, message types, conditions, "
                "or requirements, extract the individual rows/items and explain "
                "each one. Avoid replacing a table with a vague summary. "
                "The story array is mandatory for technical answers: use it "
                "for the detailed extracted items, such as each field name, "
                "code, rule, condition, or table row. If the user asks to "
                "list or explain fields, include one story item per field "
                "with its role and matching rule. Each story item must cite "
                "its source at the end, for example using the real file and "
                "page from the provided extracts. Answer in the same language "
                "as the user's question. Preserve exact labels from the "
                "source for fields, tables, message types, tags, codes, and "
                "function names, for example 'Field 32-Acquirer BIN' instead "
                "of a paraphrased name. If a role, rule, value, condition, "
                "or exception is not explicitly present in the extracts, write "
                "'not specified in the provided extracts' instead of guessing. "
                "Do not translate technical labels. Do not add fields, pages, "
                "or examples that are not present in the extracts. "
                "If the user asks about a specific field, keep the answer "
                "focused on that field. Mention another field only when the "
                "extract explicitly states a direct relation with the requested "
                "field, and explain that relation. "
                "In story items, write complete sentences or compact field entries "
                "using only values copied or directly supported by the "
                "extracts. Use an extractive style: one story item must cover "
                "one verifiable fact from one source location. Do not combine "
                "facts from different pages in the same story item. When the "
                "question asks for role, usage, matching values, or conditions, "
                "answer each requested part separately; if one part is absent "
                "from the extracts, state that it is not specified. Do not "
                "list every related source. Return only the minimum set of "
                "story items needed to answer the question, usually 2 to 4 "
                "items for a specific field or rule. Do not "
                "replace exact field names, codes, message types, table names, "
                "or values with synonyms. Do not output placeholders such as "
                "'role from source', 'rule from source', 'exact label', or "
                "similar template text. Use references only from the provided "
                "extracts."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                f"Documentation extracts:\n{context}"
            ),
        },
    ]

    try:
        content = await call_hps_ai(
            messages,
            temperature=0.1,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )
    except HpsAiConfigurationError as error:
        return build_extractive_fallback_response(
            selected_sections=selected_sections,
            error_message=str(error),
        )
    except HpsAiRequestError as error:
        return build_extractive_fallback_response(
            selected_sections=selected_sections,
            error_message=str(error),
        )

    payload = parse_ai_json(content)
    payload = await repair_atomic_response(
        payload=payload,
        question=question,
        context=context,
    )
    return normalize_agent_response(
        payload=payload,
        selected_sections=selected_sections,
        enforce_story_sources=True,
        evidence=evidence,
    )
