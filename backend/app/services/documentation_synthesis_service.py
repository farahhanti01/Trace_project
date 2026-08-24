import json
import re
from typing import Any

from app.services.hps_ai_service import call_hps_ai


RAW_FRAGMENT_PATTERNS = (
    r"\bchapter\s+\d+\b",
    r"\bdata field descriptions\b",
    r"\b\d+\.\d+\.\d+\s+attributes\b",
    r"\b\d+\.\d+\s+field\s+\d+",
    r"\bmessage type\s+processing code\b",
    r"\battributes\s+fixed\s+length\b",
    r"\btable\s+\d+",
    r"\bfigure\s+\d+",
)
TECHNICAL_INTENTS = {
    "ISO_FIELD_EXPLANATION",
    "ISO_VALUE_DECODING",
    "HSM_RESPONSE_EXPLANATION",
    "HSM_MESSAGE_DECODING",
}
DOCUMENTATION_RESPONSE_SCHEMA = (
    '{"summary": string, "sections": [{"title": string, '
    '"blocks": [{"type": "paragraph|list|table|code|key_value|callout"}], '
    '"paragraphs": string[], "items": [{"label": string, "content": string}], '
    '"content": string, "source_ids": string[]}], '
    '"issues": [{"severity": "info|warning|error", '
    '"title": string, "detail": string|null}], "recommendations": string[], '
    '"references": [{"source_id": string|null}]}'
)

KNOWLEDGE_SCHEMA = (
    '{"summary": string, "knowledge": {"role": string|null, '
    '"objective": string|null, "main_content": string|null, '
    '"utility": string|null, "format": string|null, "command": string|null, '
    '"response": string|null, "interpretation": string|null, '
    '"field_nature": "code_list|composite|bitmap|tlv|subfields|free_text|'
    'simple_numeric|other|null", '
    '"structure": [{"text": string, "source_ids": string[]}], '
    '"usage": string|null, "examples": [{"text": string, '
    '"source_ids": string[]}], "validation": [{"text": string, '
    '"source_ids": string[]}], "limitations": [{"text": string, '
    '"source_ids": string[]}], "facts": [{"topic": string, "text": string, '
    '"source_ids": string[]}]}, '
    '"references": [{"source_id": string}]}'
)


def unwrap_nested_response_payload(payload: Any) -> dict[str, Any]:
    """Recupere une reponse JSON imbriquee par erreur dans summary."""
    if isinstance(payload, str):
        return parse_json_object(payload)

    if not isinstance(payload, dict):
        return {
            "summary": clean_text(payload),
            "sections": [],
            "references": [],
        }

    summary = payload.get("summary")

    if isinstance(summary, dict):
        nested = summary
    elif isinstance(summary, str) and summary.strip().startswith("{"):
        nested = parse_json_object(summary)
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
        merged = {
            **payload,
            **nested,
            **metadata,
        }
        return merged

    return payload


def parse_json_object(content: str) -> dict[str, Any]:
    """Lit un objet JSON meme si le modele ajoute du texte parasite."""
    try:
        return unwrap_nested_response_payload(json.loads(content))
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", content, flags=re.DOTALL)

    if match:
        try:
            return unwrap_nested_response_payload(json.loads(match.group(0)))
        except json.JSONDecodeError:
            pass

    return {
        "summary": content,
        "knowledge": {},
        "references": [],
    }


def clean_text(value: Any) -> str:
    """Nettoie une connaissance sans la transformer en reponse finale."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_for_validation(value: str) -> str:
    """Normalise un texte pour les controles anti-copie."""
    return re.sub(r"\s+", " ", value.lower()).strip()


def token_set(value: str) -> set[str]:
    """Retourne les tokens significatifs pour comparer deux textes."""
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9_/-]{3,}", value.lower())
        if token not in {"the", "and", "for", "with", "dans", "pour", "les", "des"}
    }


def lexical_overlap(left: str, right: str) -> float:
    """Mesure simple de proximite lexicale entre deux textes."""
    left_tokens = token_set(left)
    right_tokens = token_set(right)

    if not left_tokens or not right_tokens:
        return 0.0

    return len(left_tokens & right_tokens) / len(left_tokens)


def is_raw_fragment(text: str) -> bool:
    """Detecte un fragment brut de PDF/OCR/tableau."""
    normalized = normalize_for_validation(text)

    if not normalized:
        return False

    if normalized.endswith("..."):
        return True

    if normalized.count("|") >= 2:
        return True

    if re.search(r"\s{4,}", text):
        return True

    return any(
        re.search(pattern, normalized, flags=re.IGNORECASE)
        for pattern in RAW_FRAGMENT_PATTERNS
    )


def sentence_count(text: str) -> int:
    """Compte approximativement les phrases d'un resume."""
    return len([
        sentence
        for sentence in re.split(r"[.!?]+", text)
        if sentence.strip()
    ])


def word_count(text: str) -> int:
    """Compte les mots utiles pour verifier la profondeur d'une section."""
    return len(re.findall(r"[A-Za-zÀ-ÿ0-9_/-]+", text or ""))


def section_text(section: dict[str, Any]) -> str:
    """Assemble le texte lisible d'une section riche."""
    parts = []
    content = clean_text(section.get("content"))

    if content:
        parts.append(content)

    paragraphs = section.get("paragraphs")

    if isinstance(paragraphs, list):
        parts.extend(clean_text(paragraph) for paragraph in paragraphs)

    items = section.get("items")

    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue

            label = clean_text(item.get("label"))
            value = clean_text(item.get("content"))

            if label or value:
                parts.append(f"{label}: {value}".strip(": "))

    blocks = section.get("blocks")

    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")

            if block_type in {"paragraph", "code", "callout"}:
                parts.append(clean_text(block.get("content")))
            elif block_type == "list":
                parts.extend(clean_text(item) for item in block.get("items") or [])
            elif block_type == "key_value":
                for item in block.get("items") or []:
                    if isinstance(item, dict):
                        parts.append(
                            f"{clean_text(item.get('label'))}: "
                            f"{clean_text(item.get('value'))}".strip(": ")
                        )
            elif block_type == "table":
                if block.get("title"):
                    parts.append(clean_text(block.get("title")))

                for row in block.get("rows") or []:
                    if isinstance(row, dict):
                        parts.extend(clean_text(value) for value in row.values())

    return clean_text(" ".join(part for part in parts if part))


def detect_user_language(question: str) -> str:
    """Detecte grossierement si la question est en francais."""
    normalized = question.lower()
    french_markers = (
        "explique",
        "quel",
        "quelle",
        "quels",
        "quelles",
        "dans",
        "résume",
        "resume",
        "donne",
        "champ",
        "rôle",
        "role",
        "utilisation",
        "valeur",
        "signifie",
    )

    return "fr" if any(marker in normalized for marker in french_markers) else "en"


def is_mostly_english(text: str) -> bool:
    """Repere une explication majoritairement anglaise dans une reponse FR."""
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())

    if len(words) < 8:
        return False

    english_markers = {
        "the",
        "and",
        "this",
        "that",
        "field",
        "contains",
        "used",
        "response",
        "request",
        "transaction",
        "account",
        "code",
        "value",
    }
    french_markers = {
        "le",
        "la",
        "les",
        "des",
        "dans",
        "pour",
        "utilise",
        "champ",
        "valeur",
        "transaction",
        "compte",
        "reponse",
    }
    english_count = sum(1 for word in words if word in english_markers)
    french_count = sum(1 for word in words if word in french_markers)

    return english_count > french_count + 3


def source_texts(raw_sections: list[dict[str, Any]]) -> list[str]:
    """Recupere les textes source pour mesurer la copie."""
    return [
        clean_text(section.get("text"))
        for section in raw_sections
        if section.get("text")
    ]


def is_too_close_to_source(
    text: str,
    raw_sections: list[dict[str, Any]],
) -> bool:
    """Bloque les faits qui copient fortement un chunk."""
    if len(text) < 90:
        return False

    normalized_text = normalize_for_validation(text)

    for source_text in source_texts(raw_sections):
        normalized_source = normalize_for_validation(source_text)

        if normalized_text and normalized_text in normalized_source:
            return True

        if lexical_overlap(normalized_text, normalized_source) >= 0.86:
            return True

    return False


def unique_values(values: list[str]) -> list[str]:
    """Dedoublonne une liste de textes en conservant l'ordre."""
    seen = set()
    unique = []

    for value in values:
        normalized = clean_text(value).lower()

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        unique.append(clean_text(value))

    return unique


def fact_text(item: dict[str, Any]) -> str:
    """Lit le texte d'un fait, quel que soit le format retourne."""
    return clean_text(item.get("fact") or item.get("text"))


def fact_category(
    item: dict[str, Any],
    default_category: str,
) -> str:
    """Lit ou assigne une categorie stable a un fait."""
    return clean_text(
        item.get("category")
        or item.get("topic")
        or default_category
    ).lower()


def facts_are_similar(left: str, right: str) -> bool:
    """Fusionne seulement les faits proches sans tout melanger."""
    if not left or not right:
        return False

    left_normalized = normalize_for_validation(left)
    right_normalized = normalize_for_validation(right)

    if left_normalized == right_normalized:
        return True

    return (
        lexical_overlap(left_normalized, right_normalized) >= 0.72
        and lexical_overlap(right_normalized, left_normalized) >= 0.55
    )


def normalize_category(value: str) -> str:
    """Regroupe les categories equivalentes pour dedupliquer."""
    normalized = normalize_for_validation(value)
    aliases = {
        "role": "role",
        "usage": "usage",
        "utilisation": "usage",
        "format": "format",
        "length": "format",
        "longueur": "format",
        "structure": "structure",
        "position": "structure",
        "positions": "structure",
        "validation": "validation",
        "reject": "validation",
        "reject_code": "validation",
        "value": "value",
        "valeur": "value",
        "values": "value",
        "code": "value",
        "codes": "value",
        "limitation": "limitation",
        "limits": "limitation",
        "example": "example",
        "exemple": "example",
    }

    for key, alias in aliases.items():
        if key in normalized:
            return alias

    return normalized or "fact"


def normalize_fact_meaning(value: str) -> str:
    """Cree une identite robuste pour les faits equivalents."""
    normalized = normalize_for_validation(value)
    replacements = (
        (r"\bprocessing code\b", "field003"),
        (r"\bfield\s*0*3\b", "field003"),
        (r"\bfield\s*0*003\b", "field003"),
        (r"\b6\s*n\b", "six numeric digits"),
        (r"\bsix\s+digits?\b", "six numeric digits"),
        (r"\bsix\s+chiffres?\b", "six numeric digits"),
        (r"\bfixed\s+length\b", "fixed"),
        (r"\blongueur\s+fixe\b", "fixed"),
        (r"\b4-bit\s+bcd\b", "bcd"),
        (r"\bthree\s+bytes?\b", "three bytes"),
        (r"\btrois\s+octets?\b", "three bytes"),
    )

    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)

    tokens = sorted(token_set(normalized))

    return " ".join(tokens)


def fact_identity(fact: dict[str, Any]) -> tuple[str, str]:
    """Identite stable de deduplication par categorie et sens."""
    return (
        normalize_category(fact_category(fact, "fact")),
        normalize_fact_meaning(fact_text(fact)),
    )


FIELD_NATURES = {
    "code_list",
    "composite",
    "bitmap",
    "tlv",
    "subfields",
    "free_text",
    "simple_numeric",
    "other",
}


def normalize_field_nature(value: Any) -> str | None:
    """Normalise la nature documentaire d'un champ ISO8583."""
    normalized = normalize_for_validation(clean_text(value))

    aliases = {
        "code_list": "code_list",
        "codes": "code_list",
        "liste de codes": "code_list",
        "response code": "code_list",
        "merchant type": "code_list",
        "pos entry": "code_list",
        "composite": "composite",
        "positions": "composite",
        "segments": "composite",
        "plusieurs sous-parties": "composite",
        "bitmap": "bitmap",
        "tlv": "tlv",
        "tag": "tlv",
        "tags": "tlv",
        "subfields": "subfields",
        "subfield": "subfields",
        "sous-champs": "subfields",
        "free text": "free_text",
        "texte libre": "free_text",
        "simple numeric": "simple_numeric",
        "numerique simple": "simple_numeric",
        "numeric simple": "simple_numeric",
        "other": "other",
    }

    for key, alias in aliases.items():
        if key in normalized:
            return alias

    return normalized if normalized in FIELD_NATURES else None


def infer_field_nature_from_knowledge(
    knowledge: dict[str, Any],
) -> str:
    """Deduit la nature du champ depuis les faits documentaires disponibles."""
    explicit = normalize_field_nature(knowledge.get("field_nature"))

    if explicit:
        return explicit

    text = normalize_for_validation(" ".join([
        clean_text(knowledge.get("role")),
        clean_text(knowledge.get("format")),
        clean_text(knowledge.get("usage")),
        *[fact_text(item) for item in knowledge.get("structure") or []],
        *[fact_text(item) for item in knowledge.get("examples") or []],
        *[fact_text(item) for item in knowledge.get("validation") or []],
        *[fact_text(item) for item in knowledge.get("facts") or []],
    ]))

    if re.search(r"\bbitmap\b|bit map|bits?\s+\d+", text):
        return "bitmap"

    if re.search(r"\btlv\b|\btags?\b|tag\s+\d+", text):
        return "tlv"

    if re.search(r"\bsubfields?\b|sous[- ]champs?|field\s+\d+\.\d+", text):
        return "subfields"

    if re.search(
        r"\bpositions?\b|positions?\s+\d+\s*[–-]\s*\d+|"
        r"\bsegments?\b|groups? of two|groupes? de deux",
        text,
    ):
        return "composite"

    if re.search(
        r"\bresponse code\b|\bcode de r[ée]ponse\b|"
        r"\bcodes? possibles?\b|\bvaleurs? possibles?\b|"
        r"\bapproval\b|\bdecline\b|\brefus\b|\bapprouv",
        text,
    ):
        return "code_list"

    if re.search(r"\bfree text\b|texte libre|descriptive", text):
        return "free_text"

    if re.search(r"\bnumeric\b|num[ée]rique|fixed length|longueur fixe", text):
        return "simple_numeric"

    return "other"


def merge_fact_lists(
    items: list[dict[str, Any]],
    default_category: str,
) -> list[dict[str, Any]]:
    """Fusionne les faits proches par categorie et regroupe leurs source_ids."""
    merged: dict[str, dict[str, Any]] = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        text = fact_text(item)
        category = fact_category(item, default_category)

        if not text:
            continue

        key = None
        identity = fact_identity({
            **item,
            "category": category,
            "fact": text,
        })

        for candidate_key, candidate in merged.items():
            if (
                candidate.get("category") == category
                and (
                    fact_identity(candidate) == identity
                    or facts_are_similar(text, candidate.get("fact", ""))
                )
            ):
                key = candidate_key
                break

        if key is None:
            key = ":".join(identity)

        source_ids = item.get("source_ids") or []

        if key not in merged:
            merged[key] = {
                "category": category,
                "fact": text,
                "text": text,
                "source_ids": [],
            }

        if isinstance(source_ids, list):
            merged[key]["source_ids"] = unique_values([
                *merged[key].get("source_ids", []),
                *[str(source_id) for source_id in source_ids if source_id],
            ])

    return list(merged.values())


def reference_metadata_from_sections(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Construit les metadonnees de references sans exposer les chunks."""
    references = []

    for section in sections:
        references.append({
            "source_id": section.get("source_id"),
            "source": section.get("source"),
            "pdf_page": section.get("page"),
            "printed_page": section.get("page_document"),
            "section": section.get("heading"),
            "sheet": section.get("sheet"),
            "paragraph": section.get("paragraph"),
        })

    return references


def validate_extracted_knowledge(
    knowledge: dict[str, Any],
    raw_sections: list[dict[str, Any]],
) -> list[str]:
    """Controle que l'extraction contient des faits et non des extraits bruts."""
    errors = []
    knowledge_body = knowledge.get("knowledge")

    if not isinstance(knowledge_body, dict):
        return ["knowledge is missing or invalid"]

    scalar_keys = (
        "role",
        "objective",
        "main_content",
        "utility",
        "format",
        "command",
        "response",
        "interpretation",
        "usage",
    )

    for key in scalar_keys:
        value = clean_text(knowledge_body.get(key))

        if value and (
            is_raw_fragment(value)
            or is_too_close_to_source(value, raw_sections)
        ):
            errors.append(f"{key} looks like a raw document fragment")

    list_keys = (
        "structure",
        "examples",
        "validation",
        "limitations",
        "facts",
    )

    for key in list_keys:
        for index, item in enumerate(knowledge_body.get(key) or []):
            if not isinstance(item, dict):
                continue

            value = fact_text(item)

            if value and (
                is_raw_fragment(value)
                or is_too_close_to_source(value, raw_sections)
            ):
                errors.append(f"{key}[{index}] looks like a raw document fragment")

    return errors


def remove_raw_extracted_fragments(
    extracted: dict[str, Any],
    raw_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Supprime localement les faits qui ressemblent a des chunks bruts."""
    knowledge = extracted.get("knowledge")

    if not isinstance(knowledge, dict):
        return extracted

    cleaned_knowledge = dict(knowledge)

    for key in (
        "role",
        "objective",
        "main_content",
        "utility",
        "format",
        "command",
        "response",
        "interpretation",
        "usage",
    ):
        value = clean_text(cleaned_knowledge.get(key))

        if value and (
            is_raw_fragment(value)
            or is_too_close_to_source(value, raw_sections)
        ):
            cleaned_knowledge[key] = None

    for key in (
        "structure",
        "examples",
        "validation",
        "limitations",
        "facts",
    ):
        cleaned_items = []

        for item in cleaned_knowledge.get(key) or []:
            if not isinstance(item, dict):
                continue

            value = fact_text(item)

            if (
                value
                and not is_raw_fragment(value)
                and not is_too_close_to_source(value, raw_sections)
            ):
                cleaned_items.append(item)

        cleaned_knowledge[key] = cleaned_items

    return {
        **extracted,
        "knowledge": cleaned_knowledge,
        "_extraction_validation_errors": validate_extracted_knowledge(
            {
                **extracted,
                "knowledge": cleaned_knowledge,
            },
            raw_sections,
        ),
    }


MIN_WORDS_BY_SECTION = {
    "role": 70,
    "rôle": 70,
    "format": 35,
    "structure": 55,
    "utilisation": 60,
    "usage": 60,
    "valeur observée": 35,
    "valeur observee": 35,
    "décodage": 45,
    "decodage": 45,
}


ALLOWED_BLOCK_TYPES = {
    "paragraph",
    "list",
    "table",
    "code",
    "key_value",
    "callout",
}


FRENCH_TABLE_LABELS = {
    "position",
    "positions",
    "valeur",
    "valeurs",
    "signification",
    "description",
    "role",
    "rôle",
    "format",
    "longueur",
    "champ",
    "message",
    "commande",
    "reponse",
    "réponse",
    "code",
    "resultat",
    "résultat",
    "statut",
    "element",
    "élément",
}


def validate_table_block(
    table: dict[str, Any],
    user_language: str = "en",
) -> list[str]:
    """Valide qu'un tableau est structure et lisible."""
    errors = []
    columns = table.get("columns")
    rows = table.get("rows")

    if not isinstance(columns, list):
        return ["table columns are missing"]

    if not isinstance(rows, list):
        return ["table rows are missing"]

    if len(columns) < 2:
        errors.append("table must have at least two columns")

    if len(columns) > 8:
        errors.append("table has more than eight columns")

    if not rows:
        errors.append("table must have at least one row")

    if len(rows) > 100:
        errors.append("table has more than one hundred rows")

    column_keys = []

    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            errors.append(f"table column {index} is invalid")
            continue

        key = clean_text(column.get("key"))
        label = clean_text(column.get("label"))

        if not key or not label:
            errors.append(f"table column {index} needs key and label")
            continue

        column_keys.append(key)

        if user_language == "fr":
            normalized_label = normalize_for_validation(label)
            label_tokens = token_set(normalized_label)

            if (
                label_tokens
                and not label_tokens & FRENCH_TABLE_LABELS
                and is_mostly_english(label)
            ):
                errors.append(f"table column {label} is not localized")

    seen_rows = set()

    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"table row {row_index} is invalid")
            continue

        row_values = []

        for key in column_keys:
            value = clean_text(row.get(key))

            if not value:
                errors.append(f"table row {row_index} misses column {key}")

            if sentence_count(value) > 2 or word_count(value) > 35:
                errors.append(f"table cell {row_index}.{key} is too long")

            row_values.append(value)

        row_signature = tuple(row_values)

        if all(not value for value in row_values):
            errors.append(f"table row {row_index} is empty")

        if row_signature in seen_rows:
            errors.append(f"table row {row_index} is duplicated")

        seen_rows.add(row_signature)

    return errors


def validate_response_block(
    block: dict[str, Any],
    index: int,
    user_language: str,
) -> list[str]:
    """Valide un bloc de section avant affichage."""
    errors = []
    block_type = block.get("type")

    if block_type not in ALLOWED_BLOCK_TYPES:
        return [f"block {index} has unsupported type"]

    if block_type in {"paragraph", "code", "callout"}:
        if not clean_text(block.get("content")):
            errors.append(f"block {index} is empty")

    if block_type == "list":
        style = block.get("style") or "bullet"
        items = block.get("items")

        if style not in {"bullet", "numbered"}:
            errors.append(f"block {index} has invalid list style")

        if not isinstance(items, list) or not any(clean_text(item) for item in items):
            errors.append(f"block {index} list is empty")

    if block_type == "key_value":
        items = block.get("items")

        if not isinstance(items, list) or not items:
            errors.append(f"block {index} key_value is empty")

        for item_index, item in enumerate(items or []):
            if not isinstance(item, dict):
                errors.append(f"block {index} key_value item {item_index} is invalid")
                continue

            if not clean_text(item.get("label")) or not clean_text(item.get("value")):
                errors.append(f"block {index} key_value item {item_index} is empty")

    if block_type == "callout":
        severity = block.get("severity") or "info"

        if severity not in {"info", "warning", "error", "success"}:
            errors.append(f"block {index} callout severity is invalid")

    if block_type == "table":
        errors.extend(validate_table_block(block, user_language=user_language))

    return errors


def validate_section_depth(
    sections: list[dict[str, Any]],
    intent: str,
) -> list[str]:
    """Repere les sections trop pauvres pour une reponse technique."""
    if intent not in TECHNICAL_INTENTS:
        return []

    errors = []

    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue

        title = clean_text(section.get("title")) or str(index)
        normalized_title = normalize_for_validation(title)
        text = section_text(section)
        words = word_count(text)
        minimum = MIN_WORDS_BY_SECTION.get(normalized_title)
        paragraphs = section.get("paragraphs")
        items = section.get("items")
        has_paragraph = (
            isinstance(paragraphs, list)
            and any(clean_text(paragraph) for paragraph in paragraphs)
        ) or bool(clean_text(section.get("content")))
        has_items = isinstance(items, list) and bool(items)

        if normalized_title in {"resume", "résumé", "pages utilisees", "pages utilisées"}:
            continue

        if words < 20:
            errors.append(f"section {title} is too short")
            continue

        if minimum and words < minimum:
            errors.append(f"section {title} lacks depth")

        if sentence_count(text) <= 1 and words < 45:
            errors.append(f"section {title} is a single generic sentence")

        if has_items and not has_paragraph:
            errors.append(f"section {title} has a list without explanation")

    return errors


def validate_final_documentation_answer(
    payload: dict[str, Any],
    intent: str,
    user_language: str,
    available_source_ids: set[str],
) -> list[str]:
    """Valide la reponse finale avant normalisation backend."""
    errors = []
    summary = clean_text(payload.get("summary"))
    sections = payload.get("sections")

    if not summary:
        errors.append("summary is empty")
    elif summary.startswith("{") and "\"sections\"" in summary:
        errors.append("summary contains nested JSON")
    elif sentence_count(summary) > 4:
        errors.append("summary has more than four sentences")

    if "pages_used" in payload:
        errors.append("pages_used is forbidden")

    if intent in TECHNICAL_INTENTS and not sections:
        errors.append("sections are required for a technical answer")

    if not isinstance(sections, list):
        sections = []

    seen_titles = set()
    section_texts = []

    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"sections[{index}] is invalid")
            continue

        title = clean_text(section.get("title"))
        content = section_text(section)
        source_ids = section.get("source_ids") or []
        blocks = section.get("blocks")

        if normalize_for_validation(title) == "details":
            errors.append("section title Details is forbidden")

        if normalize_for_validation(title) in {"pages utilisées", "pages utilisees"}:
            errors.append("section title Pages utilisées is forbidden")

        normalized_title = normalize_for_validation(title)

        if normalized_title in seen_titles:
            errors.append(f"duplicate section title: {title}")

        seen_titles.add(normalized_title)

        if not content:
            errors.append(f"section {title or index} is empty")

        if is_raw_fragment(title) or is_raw_fragment(content):
            errors.append(f"section {title or index} contains a raw fragment")

        if content.endswith("..."):
            errors.append(f"section {title or index} ends with ellipsis")

        if user_language == "fr" and is_mostly_english(content):
            errors.append(f"section {title or index} is mostly English")

        if blocks is not None:
            if not isinstance(blocks, list):
                errors.append(f"section {title or index} blocks is invalid")
            else:
                has_intro_paragraph = False

                for block_index, block in enumerate(blocks):
                    if not isinstance(block, dict):
                        errors.append(
                            f"section {title or index} block {block_index} is invalid"
                        )
                        continue

                    if (
                        block.get("type") == "paragraph"
                        and clean_text(block.get("content"))
                    ):
                        has_intro_paragraph = True

                    errors.extend(
                        f"section {title or index} {error}"
                        for error in validate_response_block(
                            block,
                            block_index,
                            user_language,
                        )
                    )

                if (
                    any(
                        isinstance(block, dict)
                        and block.get("type") in {"table", "list"}
                        for block in blocks
                    )
                    and not has_intro_paragraph
                ):
                    errors.append(
                        f"section {title or index} needs an introductory paragraph"
                    )

        if not isinstance(source_ids, list) or not source_ids:
            errors.append(f"section {title or index} has no source_ids")
        else:
            for source_id in source_ids:
                if str(source_id) not in available_source_ids:
                    errors.append(f"unknown source_id: {source_id}")

        for previous_text in section_texts:
            if (
                lexical_overlap(content, previous_text) >= 0.82
                and lexical_overlap(previous_text, content) >= 0.82
            ):
                errors.append(f"section {title or index} repeats another section")

        section_texts.append(content)

    errors.extend(validate_section_depth(sections, intent))

    for reference in payload.get("references") or []:
        if not isinstance(reference, dict):
            continue

        source_id = reference.get("source_id")

        if source_id and str(source_id) not in available_source_ids:
            errors.append(f"unknown reference source_id: {source_id}")

    return errors


def extract_explicit_topics(question: str) -> set[str]:
    """Detecte les sujets que l'utilisateur demande vraiment."""
    normalized = normalize_for_validation(question)
    topics = set()

    if re.search(r"\brole\b|\brôle\b|\bsert\b|\butilise\b|\butilisé\b", normalized):
        topics.add("role")

    if re.search(r"\bautorisation\b|\bauthorization\b|\b0100\b|\b0110\b", normalized):
        topics.add("authorization_context")

    if re.search(r"\bstructure\b|\bposition\b|\bpositions\b|\bdécoup|decoup|format\b", normalized):
        topics.add("structure")

    if re.search(r"\bformat\b|\blongueur\b|\bencod|bcd\b|\bnumeric\b", normalized):
        topics.add("format")

    if re.search(
        r"\bvaleur\b|\bvaleurs\b|\bvalue\b|\bvalues\b|\bsignifie\b|"
        r"\bsignification\b|\bcode\b|\bcodes\b|\bdecode\b|\bdécode\b",
        normalized,
    ):
        topics.add("value")

    if re.search(r"\bcontrôle\b|\bcontrole\b|\bvalidation\b|\brejet\b|\breject\b|\berreur\b", normalized):
        topics.add("validation")

    if re.search(r"\blimite\b|\blimitation\b|\bexception\b", normalized):
        topics.add("limitation")

    if re.search(r"\bcompare\b|\bdifference\b|\bdifférence\b|\bversus\b|\bvs\b", normalized):
        topics.add("comparison")

    if re.search(r"\brésume\b|\bresume\b|\boverview\b|\bprésente\b|\bpresentation\b", normalized):
        topics.add("overview")

    if not topics:
        topics.update({"role", "structure"})

    if "role" in topics and "authorization_context" in topics:
        topics.add("structure")

    return topics


EXCLUDED_FACT_TERMS = (
    "american express",
    "field 152",
    "quasi-cash",
    "quasi espèces",
    "quasi-especes",
    "mcc",
    "merchant category",
    "conditional field",
)


def fact_is_out_of_scope(
    text: str,
    explicit_topics: set[str],
) -> bool:
    """Ecarte les cas particuliers non demandes."""
    normalized = normalize_for_validation(text)

    if any(term in normalized for term in EXCLUDED_FACT_TERMS):
        return True

    if "comparison" not in explicit_topics and "mti" in normalized:
        return True

    if "validation" not in explicit_topics and re.search(
        r"\breject\b|\brejet\b|\berror\b|\berreur\b|\bcontrol\b|\bcontrôle\b|\bcontrole\b",
        normalized,
    ):
        return True

    if "limitation" not in explicit_topics and re.search(
        r"\blimitation\b|\bexception\b",
        normalized,
    ):
        return True

    return False


def allowed_knowledge_keys_for_plan(
    plan: list[dict[str, Any]],
    explicit_topics: set[str],
) -> set[str]:
    """Traduit le plan en cles de connaissance autorisees."""
    allowed = {"summary", "source_ids", "facts"}
    titles = {
        normalize_for_validation(section.get("title", ""))
        for section in plan
        if isinstance(section, dict)
    }

    if any("role" in title or "rôle" in title for title in titles):
        allowed.update({"role", "usage"})

    if any("structure" in title for title in titles):
        allowed.update({"structure", "format"})

    if any("format" in title for title in titles):
        allowed.add("format")

    if any("valeur" in title or "décodage" in title or "decodage" in title for title in titles):
        allowed.update({"examples", "facts", "structure", "format"})

    if "validation" in explicit_topics:
        allowed.add("validation")

    if "limitation" in explicit_topics:
        allowed.add("limitations")

    if "overview" in explicit_topics:
        allowed.update({"objective", "main_content", "utility", "limitations"})

    return allowed


def filter_fact_items(
    items: list[dict[str, Any]],
    explicit_topics: set[str],
) -> list[dict[str, Any]]:
    """Filtre et dedoublonne les faits avant generation."""
    filtered = []
    seen = set()

    for item in items or []:
        if not isinstance(item, dict):
            continue

        text = fact_text(item)
        category = normalize_category(fact_category(item, "fact"))

        if not text or fact_is_out_of_scope(text, explicit_topics):
            continue

        if category == "validation" and "validation" not in explicit_topics:
            continue

        if category == "limitation" and "limitation" not in explicit_topics:
            continue

        if category == "example" and "value" not in explicit_topics:
            continue

        identity = fact_identity(item)

        if identity in seen:
            continue

        seen.add(identity)
        filtered.append(item)

    return filtered


def filter_knowledge_for_plan(
    knowledge: dict[str, Any],
    plan: list[dict[str, Any]],
    question: str = "",
) -> dict[str, Any]:
    """Transmet au writer uniquement les connaissances utiles au plan."""
    explicit_topics = extract_explicit_topics(question)
    allowed_keys = allowed_knowledge_keys_for_plan(plan, explicit_topics)
    filtered = {
        "summary": knowledge.get("summary"),
        "field_nature": knowledge.get("field_nature"),
        "source_ids": knowledge.get("source_ids", []),
    }

    for key in (
        "role",
        "objective",
        "main_content",
        "utility",
        "format",
        "command",
        "response",
        "interpretation",
        "usage",
    ):
        value = clean_text(knowledge.get(key))

        if key in allowed_keys and value and not fact_is_out_of_scope(value, explicit_topics):
            filtered[key] = value

    for key in ("structure", "examples", "validation", "limitations", "facts"):
        if key in allowed_keys:
            filtered[key] = filter_fact_items(
                knowledge.get(key) or [],
                explicit_topics,
            )

    return filtered


def question_requests_table(question: str) -> bool:
    """Detecte les demandes explicites de restitution sous forme de tableau."""
    normalized = normalize_for_validation(question)

    return bool(
        re.search(
            r"\btableau\b|\btable\b|\bsous\s+forme\s+de\s+tableau\b",
            normalized,
        )
    )


def plan_requests_value_table(plan: list[dict[str, Any]]) -> bool:
    """Verifie si le plan contient une section de valeurs/codes."""
    for section in plan:
        if not isinstance(section, dict):
            continue

        title = normalize_for_validation(section.get("title", ""))

        if (
            "valeur" in title
            or "signification" in title
            or "code" in title
        ) and "table" in (section.get("preferred_blocks") or []):
            return True

    return False


def response_has_table(payload: dict[str, Any]) -> bool:
    """Indique si la reponse contient deja un tableau exploitable."""
    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue

        for block in section.get("blocks") or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "table"
                and block.get("rows")
            ):
                return True

    return False


def requested_field_numbers(question: str) -> set[str]:
    """Extrait les fields demandes pour eviter de les confondre avec des codes."""
    fields = set()

    for pattern in (
        r"\bfield\s*0*(\d+(?:\.\d+)?)\b",
        r"\bfld\s*\(?0*(\d+(?:\.\d+)?)\)?",
        r"\bchamp\s*0*(\d+(?:\.\d+)?)\b",
    ):
        for match in re.findall(pattern, question, flags=re.IGNORECASE):
            fields.add(match.lstrip("0") or "0")

    return fields


def valid_code_candidate(code: str, question: str) -> bool:
    """Filtre les faux codes issus des titres, pages ou numeros de champ."""
    normalized_code = clean_text(code).strip("[]().,:;")

    if not re.fullmatch(r"[A-Z0-9]{2,4}", normalized_code):
        return False

    if normalized_code.lower() in {"and", "the", "for", "field", "page"}:
        return False

    if normalized_code.lstrip("0") in requested_field_numbers(question):
        return False

    return bool(re.search(r"\d", normalized_code) or len(normalized_code) == 2)


def repair_pdf_letter_spacing(value: str) -> str:
    """Recolle les lettres que certains PDFs extraient separement."""
    repaired = value

    for _ in range(3):
        repaired = re.sub(r"\b([A-Z])\s+([a-z])", r"\1\2", repaired)
        repaired = re.sub(r"\b([A-Z])\s+\.", r"\1.", repaired)

    return repaired


def clean_code_meaning(value: str) -> str:
    """Nettoie une signification de code sans exposer un gros extrait brut."""
    meaning = repair_pdf_letter_spacing(value)
    meaning = re.sub(r"\bSource\s+\d+\b.*$", "", meaning, flags=re.IGNORECASE)
    meaning = re.sub(r"\bpage\s+\d+\b.*$", "", meaning, flags=re.IGNORECASE)
    meaning = clean_text(meaning).strip(" .;-:")
    meaning = re.sub(r"(?:\s+\d+){1,6}$", "", meaning).strip()

    if len(meaning) > 140:
        meaning = meaning[:140].rsplit(" ", 1)[0].strip()

    return meaning


def table_code_from_token(token: str) -> str | None:
    """Decode un code de table en ignorant les notes collees au code."""
    cleaned = clean_text(token).strip(".,;:()[]")

    if not cleaned:
        return None

    cleaned = cleaned.replace(",", "")

    if re.fullmatch(r"\d{2}", cleaned):
        return cleaned

    if re.fullmatch(r"\d{3,4}", cleaned):
        return cleaned[:2]

    if re.fullmatch(r"[A-Z]\d", cleaned):
        return cleaned

    if re.fullmatch(r"[A-Z]\d{2,3}", cleaned):
        return cleaned[:2]

    if re.fullmatch(r"[A-Z]{2}\d{1,2}", cleaned):
        return cleaned[:2]

    if re.fullmatch(r"[A-Z]\d", cleaned) or re.fullmatch(r"[A-Z]{2}", cleaned):
        return cleaned

    return None


def looks_like_table_code_token(token: str, question: str) -> bool:
    """Valide un token de code dans une table documentaire."""
    code = table_code_from_token(token)

    if not code:
        return False

    if not re.fullmatch(r"[A-Z0-9]{2,4}", code):
        return False

    if code.lower() in {"and", "the", "for", "page"}:
        return False

    return bool(re.search(r"\d", code) or len(code) == 2)


def normalize_table_code_rows(
    text: str,
    source_ids: list[str],
    question: str,
) -> list[dict[str, str]]:
    """Parse les tables PDF dont les colonnes sont collees par l'extracteur."""
    if not re.search(
        r"\bcode\b.{0,80}\bdefinition\b|\bresponse codes?\b",
        text,
        flags=re.IGNORECASE,
    ):
        return []

    candidate_text = text
    split_match = re.search(
        r"\bcode\b\s+\bdefinition\b",
        candidate_text,
        flags=re.IGNORECASE,
    )

    if split_match:
        candidate_text = candidate_text[split_match.end():]

    candidate_text = re.split(
        r"\bTable\s+\d+-\d+\b|\bFootnotes?\s+for\s+Response\s+Codes\b",
        candidate_text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    tokens = re.findall(r"[A-Z0-9,]+|[^\s]+", candidate_text)
    rows = []
    current_code = None
    current_words: list[str] = []

    def flush_current() -> None:
        if not current_code or not current_words:
            return

        meaning = clean_code_meaning(" ".join(current_words))

        if (
            meaning
            and len(meaning) >= 3
            and not is_raw_fragment(meaning)
        ):
            rows.append({
                "code": current_code,
                "meaning": meaning,
                "reference": ", ".join(source_ids),
            })

    for token in tokens:
        code = table_code_from_token(token)

        if code and looks_like_table_code_token(token, question):
            flush_current()
            current_code = code
            current_words = []
            continue

        if current_code:
            current_words.append(token)

    flush_current()

    return rows


def extract_code_rows_from_text(
    text: str,
    source_ids: list[str],
    question: str,
) -> list[dict[str, str]]:
    """Extrait generiquement des paires code/signification depuis les sources."""
    rows = normalize_table_code_rows(
        text=text,
        source_ids=source_ids,
        question=question,
    )
    patterns = (
        r"(?:response\s+code|code\s+de\s+r[ée]ponse|code)\s+"
        r"([A-Z0-9]{2,4})\s+"
        r"(?:indicates?|means?|signifie|indique|applies?|=|:|-|—)\s+"
        r"([^.;\n]+)",
        r"\b([A-Z0-9]{2,4})\b\s*(?:=|:|–|-|—)\s*([^.;\n]+)",
        r"\b([A-Z0-9]{2,4})\b\s+"
        r"(?:indicates?|means?|signifie|indique)\s+([^.;\n]+)",
    )

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            code = clean_text(match.group(1)).upper()
            meaning = clean_code_meaning(match.group(2))

            if not valid_code_candidate(code, question):
                continue

            if (
                not meaning
                or is_raw_fragment(meaning)
                or len(meaning) < 3
            ):
                continue

            rows.append({
                "code": code,
                "meaning": meaning,
                "reference": ", ".join(source_ids),
            })

    return rows


def code_rows_from_knowledge(
    knowledge: dict[str, Any],
    question: str,
) -> list[dict[str, str]]:
    """Recupere les codes depuis les faits fusionnes."""
    rows = []

    for key in ("facts", "examples", "validation", "structure"):
        for item in knowledge.get(key) or []:
            if not isinstance(item, dict):
                continue

            source_ids = [
                str(source_id)
                for source_id in item.get("source_ids") or []
                if source_id
            ]
            rows.extend(
                extract_code_rows_from_text(
                    fact_text(item),
                    source_ids,
                    question,
                )
            )

    return rows


def code_rows_from_sections(
    sections: list[dict[str, Any]],
    question: str,
) -> list[dict[str, str]]:
    """Recupere les codes depuis les extraits RAG selectionnes."""
    rows = []

    for section in sections:
        source_id = str(section.get("source_id") or "")
        text = clean_text(
            section.get("evidence_excerpt")
            or section.get("text")
            or ""
        )

        if not source_id or not text:
            continue

        rows.extend(
            extract_code_rows_from_text(
                text,
                [source_id],
                question,
            )
        )

    return rows


def dedupe_code_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Dedoublonne les lignes de codes en fusionnant leurs references."""
    merged: dict[tuple[str, str], dict[str, str]] = {}

    for row in rows:
        code = clean_text(row.get("code")).upper()
        meaning = clean_text(row.get("meaning"))
        reference = clean_text(row.get("reference"))

        if not code or not meaning:
            continue

        key = (code,)

        if key not in merged:
            merged[key] = {
                "code": code,
                "meaning": meaning,
                "reference": reference,
            }
        elif reference and reference not in merged[key]["reference"]:
            merged[key]["reference"] = ", ".join(
                value
                for value in [merged[key]["reference"], reference]
                if value
            )

    return list(merged.values())[:100]


def source_ids_from_rows(rows: list[dict[str, str]]) -> list[str]:
    """Recupere les source_ids cites dans les lignes de tableau."""
    source_ids = []

    for row in rows:
        source_ids.extend(
            token
            for token in re.findall(r"\bS\d+\b", row.get("reference", ""))
        )

    return unique_values(source_ids)


def enforce_value_table_when_requested(
    payload: dict[str, Any],
    knowledge: dict[str, Any],
    plan: list[dict[str, Any]],
    selected_sections: list[dict[str, Any]],
    question: str,
) -> dict[str, Any]:
    """Ajoute un vrai tableau de codes si la question le demande explicitement."""
    if (
        not question_requests_table(question)
        or not plan_requests_value_table(plan)
        or response_has_table(payload)
    ):
        return payload

    rows = dedupe_code_rows([
        *code_rows_from_knowledge(knowledge, question),
        *code_rows_from_sections(selected_sections, question),
    ])

    if len(rows) < 2:
        return payload

    sections = payload.get("sections")

    if not isinstance(sections, list):
        sections = []

    source_ids = source_ids_from_rows(rows)
    table_section = None

    for section in sections:
        if not isinstance(section, dict):
            continue

        title = normalize_for_validation(section.get("title", ""))

        if "valeur" in title or "signification" in title or "code" in title:
            table_section = section
            break

    if table_section is None:
        table_section = {
            "title": "Valeurs et significations",
            "source_ids": source_ids,
        }
        sections.append(table_section)

    table_section["content"] = ""
    table_section["paragraphs"] = [
        "Les codes ci-dessous proviennent des passages documentaires retrouves et indiquent les significations explicitement disponibles dans les sources."
    ]
    table_section["items"] = []
    table_section["blocks"] = [
        {
            "type": "paragraph",
            "content": table_section["paragraphs"][0],
        },
        {
            "type": "table",
            "columns": [
                {"key": "code", "label": "Code"},
                {"key": "meaning", "label": "Signification"},
                {"key": "reference", "label": "Reference"},
            ],
            "rows": rows,
        },
    ]
    table_section["source_ids"] = unique_values([
        *[str(source_id) for source_id in table_section.get("source_ids") or []],
        *source_ids,
    ])
    payload["sections"] = sections

    return payload


def block_identity(block: dict[str, Any]) -> str:
    """Identite stable pour dedupliquer les blocs."""
    return json.dumps(block, ensure_ascii=False, sort_keys=True)


def table_row_identities(block: dict[str, Any]) -> list[str]:
    """Retourne les signatures de lignes de tableau."""
    rows = block.get("rows") if isinstance(block, dict) else []

    if not isinstance(rows, list):
        return []

    return [
        json.dumps(row, ensure_ascii=False, sort_keys=True)
        for row in rows
        if isinstance(row, dict)
    ]


def key_value_identities(block: dict[str, Any]) -> list[str]:
    """Retourne les signatures des couples cle/valeur."""
    items = block.get("items") if isinstance(block, dict) else []

    if not isinstance(items, list):
        return []

    return [
        f"{normalize_for_validation(item.get('label', ''))}:"
        f"{normalize_for_validation(item.get('value', ''))}"
        for item in items
        if isinstance(item, dict)
    ]


def response_text(payload: dict[str, Any]) -> str:
    """Assemble le texte public de la reponse."""
    return clean_text(" ".join([
        clean_text(payload.get("summary")),
        *[
            section_text(section)
            for section in payload.get("sections") or []
            if isinstance(section, dict)
        ],
    ]))


def validate_response_against_plan(
    response: dict[str, Any],
    plan: list[dict[str, Any]],
    question: str = "",
    available_source_ids: set[str] | None = None,
) -> list[str]:
    """Controle que la reponse respecte le plan dynamique."""
    errors = []
    allowed_titles = {
        clean_text(section.get("title"))
        for section in plan
        if isinstance(section, dict)
    }
    allowed_section_titles = allowed_titles - {"Résumé"}
    explicit_topics = extract_explicit_topics(question)

    if "pages_used" in response:
        errors.append("pages_used is forbidden")

    if any(
        normalize_for_validation(title) == "pages utilisées"
        or normalize_for_validation(title) == "pages utilisees"
        for title in allowed_titles
    ):
        errors.append("plan contains Pages utilisées")

    seen_blocks = set()

    for section in response.get("sections") or []:
        if not isinstance(section, dict):
            continue

        title = clean_text(section.get("title"))

        if title not in allowed_section_titles:
            errors.append(f"section not planned: {title}")

        if normalize_for_validation(title) in {
            "pages utilisées",
            "pages utilisees",
            "contrôle",
            "controle",
            "limitation",
            "limites",
        }:
            if title not in allowed_section_titles:
                errors.append(f"unrequested section: {title}")

        for block in section.get("blocks") or []:
            if not isinstance(block, dict):
                continue

            identity = block_identity(block)

            if identity in seen_blocks:
                errors.append(f"duplicate block in section {title}")

            seen_blocks.add(identity)

            if block.get("type") == "table":
                rows = table_row_identities(block)

                if len(rows) != len(set(rows)):
                    errors.append(f"duplicate table rows in section {title}")

            if block.get("type") == "key_value":
                items = key_value_identities(block)

                if len(items) != len(set(items)):
                    errors.append(f"duplicate key_value items in section {title}")

        section_blocks = [
            block
            for block in section.get("blocks") or []
            if isinstance(block, dict)
        ]
        section_has_table = any(block.get("type") == "table" for block in section_blocks)
        section_plain_text = normalize_for_validation(section_text(section))

        if (
            not section_has_table
            and re.search(r"\btableau\b|\btable\b", section_plain_text)
        ):
            errors.append(f"section {title} promises a table but has no table block")

    text = normalize_for_validation(response_text(response))

    if any(term in text for term in EXCLUDED_FACT_TERMS):
        errors.append("response contains unrequested special case")

    if "comparison" not in explicit_topics and re.search(r"\bcontrairement au mti\b|\bunlike the mti\b", text):
        errors.append("response contains an unrequested MTI comparison")

    if "validation" not in explicit_topics and re.search(r"\bcodes? de rejet\b|\breject codes?\b", text):
        errors.append("response contains unrequested reject codes")

    if "limitation" not in explicit_topics and re.search(r"\blimitation\b|\blimites\b", text):
        errors.append("response contains unrequested limitations")

    if (
        "structure" in explicit_topics
        and any("structure" in normalize_for_validation(title) for title in allowed_section_titles)
        and not any(
            "structure" in normalize_for_validation(section.get("title", ""))
            for section in response.get("sections") or []
            if isinstance(section, dict)
        )
    ):
        errors.append("structure section is missing")

    if available_source_ids is not None:
        for section in response.get("sections") or []:
            if not isinstance(section, dict):
                continue

            for source_id in section.get("source_ids") or []:
                if str(source_id) not in available_source_ids:
                    errors.append(f"unknown source_id: {source_id}")

    return errors


class KnowledgeExtractor:
    """Extrait des faits documentaires bruts sans rediger la reponse finale."""

    @staticmethod
    async def extract(
        *,
        question: str,
        intent: str,
        context: str,
    ) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a knowledge extraction engine for technical "
                    "documentation. This is NOT the final user answer. Extract "
                    "only facts explicitly present in the provided extracts. "
                    "Do not write marketing text, do not explain to the user, "
                    "and do not invent. Preserve source_ids for every fact. "
                    "Return only valid JSON with this schema: "
                    f"{KNOWLEDGE_SCHEMA}. If a field is absent, use null or an "
                    "empty list. Keep facts concise and factual."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Intent:\n{intent}\n\n"
                    f"Question:\n{question}\n\n"
                    f"Documentation extracts:\n{context}"
                ),
            },
        ]
        content = await call_hps_ai(
            messages,
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )

        return parse_json_object(content)


class KnowledgeSynthesizer:
    """Fusionne les faits extraits et supprime les doublons."""

    @staticmethod
    def merge(extracted: dict[str, Any]) -> dict[str, Any]:
        knowledge = extracted.get("knowledge")

        if not isinstance(knowledge, dict):
            knowledge = {}

        merged = {
            "summary": clean_text(extracted.get("summary")),
            "role": clean_text(knowledge.get("role")) or None,
            "objective": clean_text(knowledge.get("objective")) or None,
            "main_content": clean_text(knowledge.get("main_content")) or None,
            "utility": clean_text(knowledge.get("utility")) or None,
            "format": clean_text(knowledge.get("format")) or None,
            "field_nature": normalize_field_nature(knowledge.get("field_nature")),
            "command": clean_text(knowledge.get("command")) or None,
            "response": clean_text(knowledge.get("response")) or None,
            "interpretation": clean_text(knowledge.get("interpretation")) or None,
            "usage": clean_text(knowledge.get("usage")) or None,
            "structure": merge_fact_lists(
                knowledge.get("structure") or [],
                "structure",
            ),
            "examples": merge_fact_lists(
                knowledge.get("examples") or [],
                "example",
            ),
            "validation": merge_fact_lists(
                knowledge.get("validation") or [],
                "validation",
            ),
            "limitations": merge_fact_lists(
                knowledge.get("limitations") or [],
                "limitation",
            ),
            "facts": merge_fact_lists(
                knowledge.get("facts") or [],
                "fact",
            ),
        }

        references = extracted.get("references") or []
        merged["source_ids"] = unique_values([
            str(source_id)
            for item in references
            if isinstance(item, dict)
            for source_id in [item.get("source_id")]
            if source_id
        ])

        for key in ("structure", "examples", "validation", "limitations", "facts"):
            for item in merged[key]:
                merged["source_ids"] = unique_values([
                    *merged["source_ids"],
                    *item.get("source_ids", []),
                ])

        merged["field_nature"] = infer_field_nature_from_knowledge(merged)

        return merged


class ResponsePlanner:
    """Construit le plan de reponse selon l'intention detectee."""

    @staticmethod
    def section(
        title: str,
        goals: list[str],
        preferred_blocks: list[str] | None = None,
    ) -> dict[str, Any]:
        """Cree une entree de plan avec les objectifs de redaction."""
        return {
            "title": title,
            "goals": goals,
            "preferred_blocks": preferred_blocks or [],
        }

    @staticmethod
    def enrich_block_preferences(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ajoute les formats recommandes sans les rendre obligatoires."""
        preferences = {
            "Rôle": ["paragraph"],
            "Role": ["paragraph"],
            "Format": ["paragraph", "key_value"],
        "Structure": ["paragraph", "table", "list"],
        "Valeur observée": ["paragraph", "code", "key_value"],
        "Valeurs et significations": ["paragraph", "table"],
        "Décodage": ["paragraph", "table"],
            "Utilisation": ["paragraph", "list", "callout"],
            "Commande": ["paragraph", "key_value", "code"],
            "Objectif": ["paragraph"],
            "Réponse attendue": ["paragraph", "key_value", "table"],
            "Interprétation": ["paragraph", "callout"],
            "Objectif du document": ["paragraph"],
            "Organisation générale": ["paragraph", "list"],
            "Contenu principal": ["paragraph", "list", "table"],
            "Utilité pour TRACE": ["paragraph", "list"],
            "Limites": ["paragraph", "list", "callout"],
        }

        enriched = []

        for section in plan:
            enriched.append({
                **section,
                "preferred_blocks": section.get("preferred_blocks")
                or preferences.get(section.get("title"), ["paragraph"]),
            })

        return enriched

    @staticmethod
    def build(
        *,
        intent: str,
        knowledge: dict[str, Any],
        question: str = "",
    ) -> list[dict[str, Any]]:
        explicit_topics = extract_explicit_topics(question)

        if intent in {"ISO_FIELD_EXPLANATION", "ISO_VALUE_DECODING"}:
            field_nature = infer_field_nature_from_knowledge(knowledge)
            base_plan = [
                ResponsePlanner.section(
                    "Résumé",
                    [
                        "Nommer l'element technique.",
                        "Donner son role principal.",
                        "Resumer son importance en 2 a 4 phrases.",
                    ],
                    ["paragraph"],
                )
            ]

            if "role" in explicit_topics or "authorization_context" in explicit_topics:
                base_plan.append(
                    ResponsePlanner.section(
                        "Rôle dans l’autorisation"
                        if "authorization_context" in explicit_topics
                        else "Rôle",
                        [
                            "Expliquer uniquement le role demande.",
                            "Relier le champ a la transaction d'autorisation si la question le demande.",
                            "Ne pas comparer au MTI sauf si la question demande explicitement une comparaison.",
                        ],
                        ["paragraph"],
                    )
                )

            if "format" in explicit_topics:
                base_plan.append(
                    ResponsePlanner.section(
                        "Format",
                        [
                            "Indiquer le format seulement parce que la question le demande.",
                            "Formuler naturellement en francais technique.",
                        ],
                        ["paragraph", "key_value"],
                    )
                )

            if "structure" in explicit_topics and knowledge.get("structure"):
                structure_goals = [
                    "Identifier d'abord la nature documentaire du champ.",
                    "Adapter la presentation au format reel decrit par la documentation.",
                    "Ne pas supposer une liste de codes independants sans preuve documentaire.",
                ]

                if field_nature in {"composite", "subfields", "tlv", "bitmap"}:
                    structure_goals.extend([
                        "Presenter chaque position, segment, tag, bit ou sous-champ separement.",
                        "Ne pas agreger toutes les valeurs de sous-parties differentes dans un seul tableau.",
                    ])
                elif field_nature == "code_list":
                    structure_goals.extend([
                        "Indiquer que le champ porte une valeur codee unique si la documentation le supporte.",
                        "Ne pas inventer de sous-structure absente de la documentation.",
                    ])
                else:
                    structure_goals.append(
                        "Si la documentation ne decrit pas de structure interne, le dire clairement."
                    )

                base_plan.append(
                    ResponsePlanner.section(
                        "Structure du champ",
                        structure_goals,
                        ["paragraph", "table"],
                    )
                )

            if intent == "ISO_VALUE_DECODING" or "value" in explicit_topics:
                if re.search(r"\bvaleurs?\b|\bvalues?\b|\bcodes?\b", normalize_for_validation(question)):
                    value_goals = [
                        "Presenter les valeurs ou codes demandes uniquement si la documentation les fournit.",
                        "Si aucun tableau de valeurs n'est disponible dans les connaissances, l'indiquer explicitement.",
                        "Ne pas annoncer de tableau sans retourner un block table.",
                    ]

                    if field_nature == "code_list":
                        value_goals.append(
                            "Utiliser un tableau unique Code / Signification / Reference pour les codes independants."
                        )
                    elif field_nature in {"composite", "subfields", "tlv", "bitmap"}:
                        value_goals.extend([
                            "Presenter les valeurs par sous-partie, tag, bit, position ou sous-champ.",
                            "Creer plusieurs tableaux si plusieurs sous-parties possedent chacune leurs propres valeurs.",
                            "Ne pas melanger les codes de sous-parties differentes dans un seul tableau.",
                        ])
                    else:
                        value_goals.append(
                            "Ne pas transformer artificiellement un champ simple en tableau de codes."
                        )

                    base_plan.append(
                        ResponsePlanner.section(
                            "Valeurs et significations",
                            value_goals,
                            ["paragraph", "table"],
                        )
                    )
                else:
                    base_plan.extend([
                        ResponsePlanner.section(
                            "Valeur observée",
                            [
                                "Afficher la valeur observee si elle est explicitement fournie.",
                            ],
                            ["paragraph", "code", "key_value"],
                        ),
                        ResponsePlanner.section(
                            "Décodage",
                            [
                                "Decoder la valeur uniquement selon la structure documentee.",
                            ],
                            ["paragraph", "table"],
                        ),
                    ])

            if (
                "value" in explicit_topics
                and field_nature in {"composite", "subfields", "tlv", "bitmap"}
                and knowledge.get("examples")
            ):
                base_plan.append(
                    ResponsePlanner.section(
                        "Exemple de dÃ©codage",
                        [
                            "Montrer un exemple complet uniquement si la valeur est documentee.",
                            "Interpreter chaque sous-partie selon la structure officielle.",
                            "Ne pas inventer de valeur d'exemple.",
                        ],
                        ["paragraph", "table", "code"],
                    )
                )

            if "validation" in explicit_topics and knowledge.get("validation"):
                base_plan.append(
                    ResponsePlanner.section(
                        "Contrôles",
                        [
                            "Presenter uniquement les controles explicitement demandes.",
                        ],
                        ["paragraph", "list", "table"],
                    )
                )

            if "limitation" in explicit_topics and knowledge.get("limitations"):
                base_plan.append(
                    ResponsePlanner.section(
                        "Limites",
                        [
                            "Presenter uniquement les limites explicitement demandees.",
                        ],
                        ["paragraph", "list"],
                    )
                )
        elif intent in {"HSM_RESPONSE_EXPLANATION", "HSM_MESSAGE_DECODING"}:
            base_plan = [
                ResponsePlanner.section(
                    "Résumé",
                    [
                        "Identifier la commande ou la reponse HSM.",
                        "Resumer son objectif et son resultat principal.",
                    ],
                ),
                ResponsePlanner.section(
                    "Commande",
                    [
                        "Nommer la commande HSM.",
                        "Expliquer son role documente.",
                    ],
                ),
                ResponsePlanner.section(
                    "Objectif",
                    [
                        "Expliquer pourquoi cette commande est envoyee au HSM.",
                        "Relier la commande au traitement metier.",
                    ],
                ),
                ResponsePlanner.section(
                    "Structure",
                    [
                        "Decrire uniquement la structure documentee.",
                        "Ne pas decoder de champ si la regle documentaire manque.",
                    ],
                ),
                ResponsePlanner.section(
                    "Réponse attendue",
                    [
                        "Identifier la reponse attendue et les codes retour documentes.",
                    ],
                ),
                ResponsePlanner.section(
                    "Interprétation",
                    [
                        "Expliquer le sens fonctionnel du code retour documente.",
                        "Distinguer echec fonctionnel et erreur technique.",
                    ],
                ),
            ]
        else:
            base_plan = [
                ResponsePlanner.section(
                    "Résumé",
                    [
                        "Presenter le document en 2 a 4 phrases.",
                        "Donner son sujet principal et son utilite generale.",
                    ],
                ),
                ResponsePlanner.section(
                    "Objectif du document",
                    [
                        "Expliquer pourquoi ce document existe.",
                        "Preciser le type de lecteur ou de traitement vise.",
                    ],
                ),
                ResponsePlanner.section(
                    "Organisation générale",
                    [
                        "Decrire les grandes familles de contenu.",
                        "Expliquer comment le document est structure sans recopier les titres.",
                    ],
                ),
                ResponsePlanner.section(
                    "Contenu principal",
                    [
                        "Synthetiser les sujets techniques principaux.",
                        "Regrouper les informations proches au lieu de lister les extraits.",
                    ],
                ),
                ResponsePlanner.section(
                    "Utilité pour TRACE",
                    [
                        "Expliquer comment le document peut aider l'analyse TRACE.",
                        "Relier le contenu aux logs, champs, messages ou erreurs.",
                    ],
                ),
            ]

            if "limitation" in explicit_topics and knowledge.get("limitations"):
                base_plan.append(
                    ResponsePlanner.section(
                        "Limites",
                        [
                            "Mentionner uniquement les limites explicitement supportees.",
                        ],
                    )
                )

        available_topics = {
            "Rôle": bool(knowledge.get("role")),
            "Format": bool(knowledge.get("format")),
            "Structure": bool(knowledge.get("structure")),
            "Utilisation": bool(knowledge.get("usage")),
            "Valeur observée": bool(knowledge.get("examples") or knowledge.get("facts")),
            "Décodage": bool(knowledge.get("examples") or knowledge.get("facts")),
            "Objectif du document": bool(
                knowledge.get("objective") or knowledge.get("facts")
            ),
            "Organisation générale": bool(
                knowledge.get("structure") or knowledge.get("facts")
            ),
            "Contenu principal": bool(
                knowledge.get("main_content") or knowledge.get("facts")
            ),
            "Utilité pour TRACE": bool(
                knowledge.get("utility") or knowledge.get("facts")
            ),
            "Limites": bool(knowledge.get("limitations")),
            "Commande": bool(knowledge.get("command") or knowledge.get("facts")),
            "Réponse attendue": bool(knowledge.get("response") or knowledge.get("facts")),
            "Interprétation": bool(
                knowledge.get("interpretation") or knowledge.get("facts")
            ),
        }

        filtered_plan = [
            section
            for title in base_plan
            for section in [title]
            if section["title"] == "Résumé"
            or available_topics.get(section["title"], True)
        ]

        return ResponsePlanner.enrich_block_preferences(filtered_plan)


class ResponseWriter:
    """Redige la reponse finale uniquement depuis les faits fusionnes."""

    @staticmethod
    def system_prompt() -> str:
        """Prompt strict de redaction finale."""
        return (
            "Tu es un consultant technique senior Visa/HPS. Redige la reponse "
            "finale uniquement a partir des connaissances fusionnees. Tu ne "
            "vois pas les chunks bruts et tu ne dois rien inventer. La reponse "
            "doit etre entierement dans la langue de l'utilisateur. Les labels "
            "techniques officiels peuvent rester en anglais, mais les "
            "explications doivent etre en francais lorsque la question est en "
            "francais. N'ecris jamais: Chapter 4, Data Field Descriptions, "
            "Attributes, des fragments OCR, des lignes de table collees, ou du "
            "texte termine par des points de suspension. Le resume doit "
            "contenir au maximum 4 phrases. Chaque section doit repondre a un "
            "sujet unique. Ne repete pas la meme information dans le resume et "
            "dans plusieurs sections. Ne cree jamais de section Details. Le "
            "champ summary correspond au titre Resume du plan. Ne cree pas de "
            "section Resume, References ou Pages utilisees. Ne retourne jamais "
            "pages_used. Les references sont construites par le backend depuis "
            "source_ids. Le backend les "
            "construit separement. Les sections d'analyse doivent etre "
            "developpees: utilise paragraphs[] pour des paragraphes naturels "
            "et items[] uniquement pour completer une explication, par exemple "
            "des positions de champ ou des sous-valeurs. Ne reduis pas une "
            "section a une seule phrase lorsque les connaissances fusionnees "
            "permettent d'expliquer le concept, son role, son contexte "
            "d'utilisation et ses consequences documentees. Une section "
            "technique doit generalement faire 70 a 180 mots si les sources "
            "sont riches. N'allonge jamais artificiellement une section et "
            "n'ajoute aucun fait absent des connaissances fusionnees. Le champ "
            "content est optionnel et sert seulement de compatibilite; privilegie "
            "blocks[]. Les types de blocks autorises sont: paragraph, list, "
            "table, code, key_value, callout. Utilise un tableau uniquement "
            "lorsqu'il rend la reponse plus claire qu'un paragraphe: plusieurs "
            "positions, codes, commandes, valeurs ou attributs comparables. Ne "
            "cree pas de tableau pour une seule information. Ne transforme pas "
            "artificiellement un paragraphe simple en tableau. Un tableau doit "
            "avoir au moins deux colonnes, rester lisible et etre precede d'un "
            "court paragraphe qui explique ce qu'il presente. Apres un tableau "
            "complexe, ajoute un court paragraphe d'interpretation si cela aide. "
            "Pour un Field ISO8583, determine d'abord la nature du champ depuis "
            "les connaissances fusionnees: liste de codes independants, champ "
            "compose, bitmap, TLV, sous-champs, texte libre ou numerique simple. "
            "Le format de la reponse doit suivre cette nature documentaire. Si "
            "le champ est compose de positions, segments, sous-champs, tags TLV "
            "ou bits, presente chaque partie separement et ne melange pas les "
            "valeurs de parties differentes dans un seul tableau. Si le champ "
            "est une valeur unique codee, utilise un tableau unique de codes "
            "uniquement lorsque les valeurs sont documentees. Si aucun tableau "
            "de valeurs n'est present dans les connaissances, indique-le au "
            "lieu d'inventer des codes. "
            "N'ecris jamais 'voici un tableau' ou une formulation equivalente "
            "si tu ne retournes pas aussi un block de type table dans la meme "
            "section. Si le plan demande un tableau mais que les connaissances "
            "ne permettent pas de le construire proprement, explique les "
            "valeurs en paragraphes sans annoncer de tableau. "
            "Utilise list pour des etapes, conditions ou regles multiples; code "
            "pour une valeur brute, une commande, un message ou un format; "
            "key_value pour MTI, thread, commande, reponse, code retour, statut "
            "ou nom de champ; callout pour une note importante supportee. "
            "Utilise un francais technique naturel. Prefere: 'Le Field 003 "
            "identifie la nature de l'operation et les types de comptes "
            "concernes.' Evite: 'le type de transaction du titulaire de la "
            "carte' si cette precision n'est pas supportee. Prefere: 'champ "
            "numerique fixe de six chiffres, encode sur trois octets en BCD.' "
            "Evite les formulations brutes comme '6 N, 4-bit BCD unsigned "
            "packed' sauf si tu les accompagnes d'une explication naturelle. "
            "N'ajoute aucune comparaison avec le MTI sauf si la question le "
            "demande ou si les connaissances fusionnees le supportent "
            "explicitement; si c'est une interpretation, indique-le comme tel. "
            "N'inclus pas de cas particuliers non demandes comme American "
            "Express, Field 152, quasi-especes, MCC, codes de rejet, controles "
            "ou limitations lorsque le plan ne les demande pas. "
            "paragraphs[] et items[] peuvent etre renseignes pour compatibilite, "
            "mais blocks[] est le format principal. Retourne uniquement un JSON valide avec "
            f"ce schema: {DOCUMENTATION_RESPONSE_SCHEMA}. Le modele retourne "
            "uniquement des source_ids; il ne doit jamais inventer les pages."
        )

    @staticmethod
    async def generate(
        *,
        question: str,
        intent: str,
        knowledge: dict[str, Any],
        plan: list[str],
        references: list[dict[str, Any]],
    ) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": ResponseWriter.system_prompt(),
            },
            {
                "role": "user",
                "content": (
                    f"Intent:\n{intent}\n\n"
                    f"Question:\n{question}\n\n"
                    f"Response plan:\n{json.dumps(plan, ensure_ascii=False)}\n\n"
                    "Fused knowledge:\n"
                    f"{json.dumps(knowledge, ensure_ascii=False)}\n\n"
                    "Reference metadata, without raw chunks:\n"
                    f"{json.dumps(references, ensure_ascii=False)}"
                ),
            },
        ]
        content = await call_hps_ai(
            messages,
            temperature=0.15,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )

        return parse_json_object(content)

    @staticmethod
    async def repair(
        *,
        question: str,
        intent: str,
        knowledge: dict[str, Any],
        plan: list[str],
        references: list[dict[str, Any]],
        draft_payload: dict[str, Any],
        validation_errors: list[str],
    ) -> dict[str, Any]:
        """Repare une seule fois une reponse finale invalide."""
        messages = [
            {
                "role": "system",
                "content": (
                    f"{ResponseWriter.system_prompt()} You are repairing a "
                    "draft that failed validation. Fix only the listed "
                    "validation errors. Do not add facts that are absent from "
                    "the fused knowledge."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Intent:\n{intent}\n\n"
                    f"Question:\n{question}\n\n"
                    f"Response plan:\n{json.dumps(plan, ensure_ascii=False)}\n\n"
                    "Fused knowledge:\n"
                    f"{json.dumps(knowledge, ensure_ascii=False)}\n\n"
                    "Reference metadata, without raw chunks:\n"
                    f"{json.dumps(references, ensure_ascii=False)}\n\n"
                    "Validation errors:\n"
                    f"{json.dumps(validation_errors, ensure_ascii=False)}\n\n"
                    "If a validation error says a section is too short or "
                    "lacks depth, develop only that section from the fused "
                    "knowledge. Keep the summary short. Do not add facts absent "
                    "from the fused knowledge.\n\n"
                    "Draft JSON:\n"
                    f"{json.dumps(draft_payload, ensure_ascii=False)}"
                ),
            },
        ]
        content = await call_hps_ai(
            messages,
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )

        return parse_json_object(content)


class KnowledgeGenerationPipeline:
    """Orchestre extraction, fusion, planification et generation finale."""

    @staticmethod
    async def generate(
        *,
        question: str,
        intent: str,
        context: str,
        selected_sections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        extracted = await KnowledgeExtractor.extract(
            question=question,
            intent=intent,
            context=context,
        )
        extraction_errors = validate_extracted_knowledge(
            knowledge=extracted,
            raw_sections=selected_sections,
        )

        if extraction_errors:
            extracted = remove_raw_extracted_fragments(
                extracted=extracted,
                raw_sections=selected_sections,
            )

        knowledge = KnowledgeSynthesizer.merge(extracted)
        plan = ResponsePlanner.build(
            intent=intent,
            knowledge=knowledge,
            question=question,
        )
        knowledge = filter_knowledge_for_plan(
            knowledge=knowledge,
            plan=plan,
            question=question,
        )
        references = reference_metadata_from_sections(selected_sections)
        available_source_ids = {
            str(reference.get("source_id"))
            for reference in references
            if reference.get("source_id")
        }
        user_language = detect_user_language(question)

        payload = await ResponseWriter.generate(
            question=question,
            intent=intent,
            knowledge=knowledge,
            plan=plan,
            references=references,
        )
        payload = enforce_value_table_when_requested(
            payload=payload,
            knowledge=knowledge,
            plan=plan,
            selected_sections=selected_sections,
            question=question,
        )
        validation_errors = validate_final_documentation_answer(
            payload=payload,
            intent=intent,
            user_language=user_language,
            available_source_ids=available_source_ids,
        )
        validation_errors.extend(
            validate_response_against_plan(
                response=payload,
                plan=plan,
                question=question,
                available_source_ids=available_source_ids,
            )
        )

        if validation_errors:
            payload = await ResponseWriter.repair(
                question=question,
                intent=intent,
                knowledge=knowledge,
                plan=plan,
                references=references,
                draft_payload=payload,
                validation_errors=validation_errors,
            )
            payload = enforce_value_table_when_requested(
                payload=payload,
                knowledge=knowledge,
                plan=plan,
                selected_sections=selected_sections,
                question=question,
            )
            payload["_validation_errors_before_repair"] = validation_errors
            repair_validation_errors = validate_final_documentation_answer(
                payload=payload,
                intent=intent,
                user_language=user_language,
                available_source_ids=available_source_ids,
            )
            repair_validation_errors.extend(
                validate_response_against_plan(
                    response=payload,
                    plan=plan,
                    question=question,
                    available_source_ids=available_source_ids,
                )
            )

            if repair_validation_errors:
                payload["_validation_errors_after_repair"] = repair_validation_errors

        payload = unwrap_nested_response_payload(payload)
        payload["_knowledge"] = knowledge
        payload["_plan"] = plan
        payload["_extraction_validation_errors"] = extraction_errors

        return payload
