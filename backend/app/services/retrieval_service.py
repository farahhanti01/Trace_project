import math
import re
import unicodedata
from typing import Any

from app.services.hps_ai_service import (
    HpsAiConfigurationError,
    HpsAiRequestError,
    create_hps_embeddings,
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

FRONT_MATTER_PATTERNS = [
    "all rights reserved",
    "confidential",
    "copyright",
    "disclaimer",
    "document hierarchy",
    "document history",
    "legal notice",
    "proprietary",
    "revision history",
    "table of contents",
    "visa confidential",
]

TECHNICAL_PATTERNS = [
    "authorization",
    "authorisation",
    "business rule",
    "code",
    "data element",
    "data field",
    "error",
    "exigence",
    "exigences",
    "exception",
    "field",
    "function",
    "iso",
    "message",
    "mti",
    "nok",
    "processing",
    "request",
    "requirement",
    "requirements",
    "response",
    "routing",
    "specification",
    "specifications",
    "technique",
    "technical",
    "tlv",
    "transaction",
]


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


def lexical_score(
    question: str,
    terms: list[str],
    text: str,
) -> float:
    searchable_text = normalize_for_search(text)
    searchable_question = normalize_for_search(question)
    score = 0.0

    for term in terms:
        occurrences = searchable_text.count(term)

        if occurrences:
            score += min(occurrences, 8)

    for phrase in re.findall(
        r'"([^"]+)"',
        searchable_question,
    ):
        if phrase and phrase in searchable_text:
            score += 10

    return score


def front_matter_penalty(
    section: dict[str, Any],
) -> float:
    combined_text = " ".join(
        [
            section.get("heading") or "",
            section.get("text") or "",
        ]
    )
    text = normalize_for_search(combined_text)
    page = section.get("page")
    paragraph = section.get("paragraph")

    penalty = 0.0

    if any(pattern in text for pattern in FRONT_MATTER_PATTERNS):
        penalty += 0.55

    if isinstance(page, int) and page <= 3:
        penalty += 0.25

    if isinstance(paragraph, int) and paragraph <= 5:
        penalty += 0.1

    return min(penalty, 0.85)


def technical_boost(
    question: str,
    section: dict[str, Any],
) -> float:
    searchable_question = normalize_for_search(question)
    searchable_text = normalize_for_search(
        " ".join(
            [
                section.get("heading") or "",
                section.get("text") or "",
            ]
        )
    )

    question_is_technical = any(
        pattern in searchable_question
        for pattern in TECHNICAL_PATTERNS
    )

    if not question_is_technical:
        return 0.0

    matches = sum(
        1
        for pattern in TECHNICAL_PATTERNS
        if pattern in searchable_text
    )

    return min(matches * 0.04, 0.25)


def exact_token_boost(
    question: str,
    section: dict[str, Any],
) -> float:
    tokens = extract_exact_tokens(question)

    if not tokens:
        return 0.0

    searchable_text = normalize_for_search(
        section_search_text(section)
    )

    matches = 0

    for token in tokens:
        compact_token = token.replace(" ", "")
        compact_text = searchable_text.replace(" ", "")

        if token in searchable_text or compact_token in compact_text:
            matches += 1

    if not matches:
        return 0.0

    coverage = matches / len(tokens)
    return 0.35 + min(coverage * 0.45, 0.45)


def exact_token_coverage(
    question: str,
    section: dict[str, Any],
) -> float:
    tokens = extract_exact_tokens(question)

    if not tokens:
        return 0.0

    searchable_text = normalize_for_search(
        section_search_text(section)
    )
    compact_text = searchable_text.replace(" ", "")
    matches = 0

    for token in tokens:
        compact_token = token.replace(" ", "")

        if token in searchable_text or compact_token in compact_text:
            matches += 1

    return matches / len(tokens)


def term_coverage(
    terms: list[str],
    section: dict[str, Any],
) -> float:
    if not terms:
        return 0.0

    searchable_text = normalize_for_search(
        section_search_text(section)
    )
    matched_terms = {
        term
        for term in terms
        if term in searchable_text
    }

    return len(matched_terms) / len(terms)


def proximity_score(
    terms: list[str],
    section: dict[str, Any],
) -> float:
    query_terms = [
        term
        for term in terms
        if len(term) >= 3
    ][:8]

    if len(query_terms) < 2:
        return 0.0

    searchable_text = normalize_for_search(
        section_search_text(section)
    )
    positions = []

    for term in query_terms:
        position = searchable_text.find(term)

        if position >= 0:
            positions.append(position)

    if len(positions) < 2:
        return 0.0

    spread = max(positions) - min(positions)

    if spread <= 400:
        return 0.25

    if spread <= 900:
        return 0.14

    if spread <= 1_600:
        return 0.07

    return 0.0


def heading_match_score(
    terms: list[str],
    section: dict[str, Any],
) -> float:
    heading = normalize_for_search(
        section.get("heading") or ""
    )

    if not heading or not terms:
        return 0.0

    matches = sum(
        1
        for term in terms
        if term in heading
    )

    return min(matches * 0.08, 0.24)


def rerank_sections(
    question: str,
    ranked_sections: list[tuple[float, float, float, dict[str, Any]]],
) -> list[tuple[float, float, float, dict[str, Any]]]:
    terms = extract_terms(question)
    reranked = []

    for base_score, lexical, vector, section in ranked_sections:
        coverage = term_coverage(terms, section)
        exact_coverage = exact_token_coverage(question, section)
        proximity = proximity_score(terms, section)
        heading_match = heading_match_score(terms, section)

        rerank_score = (
            base_score
            + (0.35 * coverage)
            + (0.45 * exact_coverage)
            + proximity
            + heading_match
        )

        enriched_section = {
            **section,
            "rerank_score": round(rerank_score, 4),
            "term_coverage": round(coverage, 4),
            "exact_token_coverage": round(exact_coverage, 4),
        }

        reranked.append((rerank_score, lexical, vector, enriched_section))

    reranked.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2],
        ),
        reverse=True,
    )

    return reranked


def split_excerpt_units(
    text: str,
) -> list[str]:
    units = [
        unit.strip()
        for unit in re.split(r"(?<=[.!?])\s+|\n+", text)
        if unit.strip()
    ]

    return units or [text.strip()]


def evidence_unit_score(
    question: str,
    unit: str,
) -> float:
    terms = extract_terms(question)
    tokens = extract_exact_tokens(question)
    searchable_unit = normalize_for_search(unit)
    compact_unit = searchable_unit.replace(" ", "")
    score = 0.0

    for term in terms:
        if term in searchable_unit:
            score += 1.0

    for token in tokens:
        compact_token = token.replace(" ", "")

        if token in searchable_unit or compact_token in compact_unit:
            score += 3.0

    return score


def extract_relevant_excerpt(
    question: str,
    text: str,
    max_characters: int = 650,
) -> str:
    cleaned_text = re.sub(r"\s+", " ", text).strip()

    if len(cleaned_text) <= max_characters:
        return cleaned_text

    units = split_excerpt_units(text)
    scored_units = [
        (evidence_unit_score(question, unit), index, unit)
        for index, unit in enumerate(units)
    ]
    scored_units.sort(
        key=lambda item: (
            item[0],
            -item[1],
        ),
        reverse=True,
    )

    selected_indexes = sorted(
        index
        for score, index, _ in scored_units[:4]
        if score > 0
    )

    if not selected_indexes:
        return f"{cleaned_text[:max_characters].rstrip()}..."

    excerpt_units = []
    current_size = 0

    for index in selected_indexes:
        unit = re.sub(r"\s+", " ", units[index]).strip()

        if not unit:
            continue

        if excerpt_units and current_size + len(unit) + 1 > max_characters:
            break

        excerpt_units.append(unit)
        current_size += len(unit) + 1

    excerpt = " ".join(excerpt_units).strip()

    if not excerpt:
        return f"{cleaned_text[:max_characters].rstrip()}..."

    return excerpt


def section_search_text(
    section: dict[str, Any],
) -> str:
    return " ".join(
        [
            section.get("heading") or "",
            section.get("text") or "",
        ]
    )


def cosine_similarity(
    left: list[float],
    right: list[float],
) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    dot_product = sum(
        left_value * right_value
        for left_value, right_value in zip(left, right)
    )

    left_norm = math.sqrt(
        sum(value * value for value in left)
    )
    right_norm = math.sqrt(
        sum(value * value for value in right)
    )

    if not left_norm or not right_norm:
        return 0.0

    return dot_product / (left_norm * right_norm)


def normalize_scores(
    values: list[float],
) -> list[float]:
    if not values:
        return []

    minimum = min(values)
    maximum = max(values)

    if maximum == minimum:
        return [
            1.0 if value > 0 else 0.0
            for value in values
        ]

    return [
        (value - minimum) / (maximum - minimum)
        for value in values
    ]


async def embed_query(
    question: str,
) -> list[float] | None:
    try:
        embeddings = await create_hps_embeddings([question])
    except (HpsAiConfigurationError, HpsAiRequestError):
        return None

    return embeddings[0] if embeddings else None


async def select_relevant_sections(
    question: str,
    sections: list[dict[str, Any]],
    limit: int = 8,
    use_embeddings: bool = True,
) -> list[dict[str, Any]]:
    if not sections:
        return []

    terms = extract_terms(question)
    query_embedding = await embed_query(question) if use_embeddings else None

    lexical_values = [
        lexical_score(
            question=question,
            terms=terms,
            text=section_search_text(section),
        )
        for section in sections
    ]

    vector_values = [
        cosine_similarity(
            query_embedding,
            section.get("embedding"),
        )
        if query_embedding and section.get("embedding")
        else 0.0
        for section in sections
    ]

    normalized_lexical = normalize_scores(lexical_values)
    normalized_vector = normalize_scores(vector_values)

    ranked = []

    for index, section in enumerate(sections):
        lexical = normalized_lexical[index]
        vector = normalized_vector[index]

        score = (0.45 * lexical) + (0.55 * vector)

        if lexical_values[index] > 0:
            score += 0.08

        score += exact_token_boost(question, section)
        score += technical_boost(question, section)
        score -= front_matter_penalty(section)

        ranked.append((score, lexical_values[index], vector_values[index], section))

    ranked.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2],
        ),
        reverse=True,
    )
    ranked = rerank_sections(
        question=question,
        ranked_sections=ranked[: max(limit * 6, limit)],
    )

    selected = []
    seen = set()

    for score, lexical, vector, section in ranked:
        if score <= 0:
            if selected:
                break

            if front_matter_penalty(section) >= 0.55:
                continue

            if lexical <= 0 and vector <= 0:
                continue

        identity = (
            section.get("source"),
            section.get("page"),
            section.get("sheet"),
            section.get("paragraph"),
            section.get("section_index"),
            section.get("chunk_index"),
        )

        if identity in seen:
            continue

        enriched_section = {
            **section,
            "retrieval_score": round(score, 4),
            "lexical_score": lexical,
            "vector_score": round(vector, 4),
        }

        selected.append(enriched_section)
        seen.add(identity)

        if len(selected) >= limit:
            break

    if selected:
        return selected

    non_front_matter = [
        section
        for section in sections
        if front_matter_penalty(section) < 0.55
    ]

    return (
        non_front_matter[:limit]
        if non_front_matter
        else sections[:limit]
    )
