from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.database import (
    document_sections_collection,
    documents_collection,
    function_catalog_collection,
)


logger = logging.getLogger(__name__)

FUNCTION_TOKEN_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
FUNCTION_CONTEXT_PATTERN = re.compile(
    r"\b(?:fonction|function|procedure|proc|appel|call|start|end)\b",
    flags=re.I,
)
FUNCTION_EXPLICIT_PATTERN = re.compile(
    r"\b(?:fonction|function)\s+([A-Za-z_][A-Za-z0-9_]{2,})(?:\s*\(\))?",
    flags=re.I,
)
ERROR_CASE_PATTERN = re.compile(
    r"\b("
    r"exception|erreur|error|failed|failure|echec|echec|nok|"
    r"no_data_found|not\s+found|aucune\s+donnee|aucune\s+donn.e|"
    r"result\s*=\s*-?\d+|return\s*code|sqlcode|ora-\d+"
    r")\b",
    flags=re.I,
)
STATUS_BLOCK_HEADER_PATTERN = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z _/-]*?)\s*\(\s*(?P<code>-?\d+)\s*\)\s*:?\s*$",
    flags=re.I,
)
INLINE_STATUS_BLOCK_PATTERN = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z _/-]*?)"
    r"(?:\s*\(\s*(?P<code>-?\d+)\s*\))?\s*:\s*(?P<detail>.+)$",
    flags=re.I,
)
KNOWN_STATUS_HEADER_PATTERN = re.compile(
    r"^\s*(?P<label>OK|SUCCESS|NOK|ERROR|ERR|KO|FAILED|FAILURE|EXCEPTION)"
    r"\s*(?:\(\s*(?P<code>-?\d+)\s*\))?\s*:?\s*$",
    flags=re.I,
)
KNOWN_STATUS_LABELS = {
    "OK",
    "SUCCESS",
    "NOK",
    "ERROR",
    "ERR",
    "KO",
    "FAILED",
    "FAILURE",
    "EXCEPTION",
    "SYSTEM_MALFUNCTION",
}
EXAMPLE_PATTERN = re.compile(
    r"\b(exemple|example|start\s+[A-Za-z_][A-Za-z0-9_]+|end\s+[A-Za-z_][A-Za-z0-9_]+|scenario|cas\s+concret)\b",
    flags=re.I,
)
EXCEPTIONS_QUERY_PATTERN = re.compile(
    r"\b(exceptions?|erreurs?|errors?|echecs?|failed|failure|nok|cas\s+d.erreur)\b",
    flags=re.I,
)
EXAMPLE_QUERY_PATTERN = re.compile(
    r"\b(exemples?|examples?|concret|scenario|cas\s+concret)\b",
    flags=re.I,
)

COMMON_NON_FUNCTION_TOKENS = {
    "field",
    "function",
    "fonction",
    "document",
    "chapter",
    "section",
    "table",
    "source",
    "status",
    "result",
    "return",
    "value",
    "error",
    "description",
    "exception",
    "exemple",
    "example",
}


def normalize_identifier(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9_]+", "", text.lower())


def compact_text(value: Any, max_characters: int = 2_000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()

    if len(text) <= max_characters:
        return text

    return text[: max_characters - 3].rstrip() + "..."


def as_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except Exception:
        return None


def split_observations(text: str) -> list[str]:
    parts = re.split(r"\s+\|\s+|(?<=[.;])\s+|\n+", text)
    return [
        compact_text(part, 450)
        for part in parts
        if clean_observation(part)
    ]


def clean_observation(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" -|")


def clean_status_detail(value: Any) -> str:
    text = clean_observation(value)
    text = re.sub(r"^[*-]\s*", "", text).strip()
    return text


def match_status_header(value: Any) -> re.Match[str] | None:
    text = clean_observation(value)
    return (
        STATUS_BLOCK_HEADER_PATTERN.match(text)
        or KNOWN_STATUS_HEADER_PATTERN.match(text)
    )


def status_block_title(label: str, code: str | None) -> str:
    normalized_label = clean_observation(label).upper()

    if code is None:
        return normalized_label

    return f"{normalized_label} ({code})"


def is_exception_status(label: str, code: str | None) -> bool:
    normalized_label = clean_observation(label).upper()

    if normalized_label in {"OK", "SUCCESS"}:
        return False

    return code != "0"


def split_structured_line(text: str) -> list[str]:
    if "|" not in text:
        return [text]

    return [
        part.strip()
        for part in text.split("|")
        if part.strip()
    ]


def inline_status_match(value: Any) -> re.Match[str] | None:
    match = INLINE_STATUS_BLOCK_PATTERN.match(clean_observation(value))

    if not match:
        return None

    label = clean_observation(match.group("label")).upper()

    if label not in KNOWN_STATUS_LABELS:
        return None

    return match


def looks_like_function_name(value: str) -> bool:
    name = str(value or "").strip().strip("()")

    if len(name) < 3:
        return False

    normalized = normalize_identifier(name)

    if not normalized or normalized in COMMON_NON_FUNCTION_TOKENS:
        return False

    if name.isupper():
        return False

    has_camel_case = bool(re.search(r"[a-z][A-Z]", name))
    has_underscore = "_" in name
    has_alpha = bool(re.search(r"[A-Za-z]", name))
    starts_valid = bool(re.match(r"^[A-Za-z_]", name))

    return starts_valid and has_alpha and (has_camel_case or has_underscore)


def extract_function_names(text: str) -> list[str]:
    text = text or ""

    names: list[str] = []
    leading_cell = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]{2,})\s*(?:\||:|-)", text)

    if leading_cell and looks_like_function_name(leading_cell.group(1)):
        names.append(leading_cell.group(1))

    if names:
        return list(dict.fromkeys(names))

    if not FUNCTION_CONTEXT_PATTERN.search(text):
        return []

    for token in FUNCTION_TOKEN_PATTERN.findall(text):
        cleaned = token.strip().strip("()")

        if looks_like_function_name(cleaned):
            names.append(cleaned)

    return list(dict.fromkeys(names))


def extract_function_name_from_question(question: str) -> str | None:
    explicit_match = FUNCTION_EXPLICIT_PATTERN.search(question or "")

    if explicit_match and looks_like_function_name(explicit_match.group(1)):
        return explicit_match.group(1)

    if not re.search(r"\b(fonction|function)\b", question or "", flags=re.I):
        return None

    for token in FUNCTION_TOKEN_PATTERN.findall(question or ""):
        if looks_like_function_name(token):
            return token

    return None


def extract_error_cases(text: str) -> list[str]:
    observations = []

    for block in extract_status_blocks(text):
        if block.get("is_exception"):
            observations.append(format_status_block(block))

    for part in split_observations(text):
        if match_status_header(part):
            continue

        if ERROR_CASE_PATTERN.search(part):
            observations.append(part)

    return list(dict.fromkeys(observations))


def extract_status_blocks(text: str) -> list[dict[str, Any]]:
    """Preserve documented return-code blocks such as NOK (-1), OK (0), ERROR (-2)."""

    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in str(text or "").splitlines():
        for line in split_structured_line(raw_line.strip()):
            if not line:
                continue

            header_match = match_status_header(line)

            if header_match:
                if current:
                    blocks.append(current)

                label = clean_observation(header_match.group("label")).upper()
                code = header_match.group("code")
                current = {
                    "label": label,
                    "code": code,
                    "title": status_block_title(label, code),
                    "details": [],
                    "is_exception": is_exception_status(label, code),
                }
                continue

            inline_match = inline_status_match(line)

            if inline_match:
                if current:
                    blocks.append(current)

                label = clean_observation(inline_match.group("label")).upper()
                code = inline_match.group("code")
                detail = clean_status_detail(inline_match.group("detail"))
                current = {
                    "label": label,
                    "code": code,
                    "title": status_block_title(label, code),
                    "details": [detail] if detail else [],
                    "is_exception": is_exception_status(label, code),
                }
                continue

            if current:
                detail = clean_status_detail(line)

                if detail:
                    current["details"].append(detail)

    if current:
        blocks.append(current)

    return blocks


def format_status_block(block: dict[str, Any]) -> str:
    details = [
        detail
        for detail in block.get("details") or []
        if detail
    ]

    if not details:
        return str(block.get("title") or "").strip()

    return f"{block.get('title')}: " + " ; ".join(details)


def extract_examples(text: str) -> list[str]:
    observations = []

    for part in split_observations(text):
        if EXAMPLE_PATTERN.search(part):
            observations.append(part)

    return list(dict.fromkeys(observations))


def reference_from_entry(entry: dict[str, Any], source_id: str | None = None) -> dict[str, Any]:
    reference: dict[str, Any] = {
        "source": entry.get("source") or "Document",
        "original_source": entry.get("source"),
        "section": entry.get("heading"),
        "sheet": entry.get("sheet"),
        "paragraph": entry.get("paragraph") or entry.get("row_number"),
        "source_id": source_id,
    }

    if entry.get("page") is not None:
        reference["page"] = entry.get("page")
        reference["pdf_page"] = entry.get("page")

    return {
        key: value
        for key, value in reference.items()
        if value is not None
    }


def deduplicate_references(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    unique_references = []

    for reference in references:
        identity = (
            reference.get("source"),
            reference.get("page"),
            reference.get("sheet"),
            reference.get("paragraph"),
            reference.get("section"),
        )

        if identity in seen:
            continue

        seen.add(identity)
        unique_references.append(reference)

    return unique_references


def ordered_cell_values(entry: dict[str, Any]) -> list[str]:
    cells = entry.get("cells") or []

    if not isinstance(cells, list):
        return []

    ordered_cells = sorted(
        [
            cell
            for cell in cells
            if isinstance(cell, dict) and cell.get("value") is not None
        ],
        key=lambda cell: int(cell.get("column") or 0),
    )

    return [
        clean_observation(cell.get("value"))
        for cell in ordered_cells
        if clean_observation(cell.get("value"))
    ]


def looks_like_source_path(value: str) -> bool:
    text = str(value or "").strip()

    return bool(
        re.search(r"[\\/]", text)
        or re.search(r"\.(?:c|pc|h|java|py|sql|pkg|pks|pkb)$", text, flags=re.I)
    )


def looks_like_library_name(value: str) -> bool:
    text = str(value or "").strip()

    return bool(re.match(r"^lib[A-Za-z0-9_-]+$", text))


def strip_status_suffix(value: str) -> str:
    text = clean_observation(value)
    chunks = split_structured_line(text)
    kept = []

    for chunk in chunks:
        if match_status_header(chunk) or inline_status_match(chunk):
            break

        kept.append(chunk)

    cleaned = " | ".join(kept).strip()
    cleaned = re.split(
        r"\s+(?:NOK|OK|SUCCESS|ERROR|ERR|KO|FAILED|FAILURE|EXCEPTION|SYSTEM_MALFUNCTION)"
        r"\s*(?:\(-?\d+\))?\s*:",
        cleaned,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" |")

    return cleaned


def function_overview(entry: dict[str, Any], function_name: str) -> dict[str, Any]:
    values = ordered_cell_values(entry)
    metadata: dict[str, str] = {}
    description_candidates: list[str] = []
    normalized_function_name = normalize_identifier(function_name)

    for index, value in enumerate(values):
        if not value:
            continue

        if index == 0 and normalize_identifier(value) == normalized_function_name:
            continue

        if looks_like_library_name(value) and "library" not in metadata:
            metadata["library"] = value
            continue

        if looks_like_source_path(value) and "source_path" not in metadata:
            metadata["source_path"] = value
            continue

        if match_status_header(value) or inline_status_match(value):
            continue

        description_candidates.append(value)

    if not description_candidates and entry.get("description"):
        raw_parts = split_structured_line(str(entry.get("description") or ""))
        description_candidates = [
            part
            for part in raw_parts
            if normalize_identifier(part) != normalized_function_name
            and not looks_like_library_name(part)
            and not looks_like_source_path(part)
            and not match_status_header(part)
            and not inline_status_match(part)
        ]

    description = ""

    for candidate in description_candidates:
        cleaned = strip_status_suffix(candidate)

        if cleaned and len(cleaned) > len(description):
            description = cleaned

    return {
        "description": description or (
            f"La fonction {function_name} est presente dans la documentation."
        ),
        "metadata": metadata,
    }


def merged_function_overview(
    entries: list[dict[str, Any]],
    function_name: str,
) -> dict[str, Any]:
    overviews = [
        function_overview(entry, function_name)
        for entry in entries
    ]
    description = next(
        (
            overview["description"]
            for overview in overviews
            if overview.get("description")
            and "est presente dans la documentation" not in overview["description"]
        ),
        f"La fonction {function_name} est presente dans la documentation.",
    )
    metadata: dict[str, str] = {}

    for overview in overviews:
        for key, value in (overview.get("metadata") or {}).items():
            metadata.setdefault(key, value)

    return {
        "description": description,
        "metadata": metadata,
    }


class FunctionCatalogExtractor:
    """Extract a best-effort catalog of documented functions from document sections."""

    @staticmethod
    def extract(
        *,
        sections: list[dict[str, Any]],
        document: dict[str, Any],
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        document_id = str(document.get("_id") or document.get("id") or "")
        source = (
            document.get("original_filename")
            or document.get("stored_filename")
            or document.get("name")
            or "Document"
        )
        conversation_id = document.get("conversation_id")
        entries = []
        seen = set()

        for section in sections:
            text = str(section.get("text") or "")

            if not text:
                continue

            function_names = extract_function_names(text)

            if not function_names:
                continue

            error_cases = extract_error_cases(text)
            status_blocks = extract_status_blocks(text)
            examples = extract_examples(text)
            cells = section.get("cells") or []

            for function_name in function_names:
                normalized_function_name = normalize_identifier(function_name)
                identity = (
                    normalized_function_name,
                    section.get("sheet"),
                    section.get("paragraph") or section.get("row_number"),
                    section.get("page"),
                    section.get("section_index"),
                    section.get("chunk_index"),
                )

                if identity in seen:
                    continue

                seen.add(identity)
                entries.append({
                    "document_id": document_id,
                    "conversation_id": conversation_id,
                    "source": source,
                    "function_name": function_name,
                    "normalized_function_name": normalized_function_name,
                    "description": compact_text(text),
                    "exceptions": error_cases,
                    "status_blocks": status_blocks,
                    "examples": examples,
                    "cells": cells,
                    "sheet": section.get("sheet"),
                    "paragraph": section.get("paragraph"),
                    "row_number": section.get("row_number") or section.get("paragraph"),
                    "page": section.get("page"),
                    "heading": section.get("heading"),
                    "section_index": section.get("section_index"),
                    "chunk_index": section.get("chunk_index"),
                    "source_section_id": (
                        str(section.get("_id"))
                        if section.get("_id") is not None
                        else None
                    ),
                    "source_chunk_id": (
                        f"{document_id}:{section.get('section_index')}:{section.get('chunk_index')}"
                    ),
                    "created_at": now,
                })

        return entries


async def find_function_catalog_entries(
    *,
    function_name: str,
    conversation_id: str | None = None,
    referenced_document_ids: list[str] | None = None,
    limit: int = 16,
) -> list[dict[str, Any]]:
    normalized_function_name = normalize_identifier(function_name)

    if not normalized_function_name:
        return []

    query: dict[str, Any] = {
        "normalized_function_name": normalized_function_name,
    }

    if referenced_document_ids:
        query["document_id"] = {
            "$in": [
                str(document_id)
                for document_id in referenced_document_ids
                if document_id
            ]
        }
    elif conversation_id:
        query["conversation_id"] = conversation_id

    entries = await function_catalog_collection.find(query).sort([
        ("source", 1),
        ("sheet", 1),
        ("paragraph", 1),
        ("page", 1),
    ]).to_list(length=limit)

    should_rescan_sections = not entries or not any(
        entry.get("status_blocks")
        for entry in entries
    )

    if should_rescan_sections:
        section_entries = await find_function_entries_from_sections(
            function_name=function_name,
            conversation_id=conversation_id,
            referenced_document_ids=referenced_document_ids,
            limit=limit,
        )

        if section_entries:
            entries = section_entries

    return entries


async def find_function_entries_from_sections(
    *,
    function_name: str,
    conversation_id: str | None = None,
    referenced_document_ids: list[str] | None = None,
    limit: int = 16,
) -> list[dict[str, Any]]:
    if not function_name:
        return []

    section_query: dict[str, Any] = {
        "text": {
            "$regex": re.escape(function_name),
            "$options": "i",
        }
    }

    if referenced_document_ids:
        section_query["document_id"] = {
            "$in": [
                str(document_id)
                for document_id in referenced_document_ids
                if document_id
            ]
        }
    elif conversation_id:
        conversation_documents = await documents_collection.find(
            {
                "conversation_id": conversation_id,
            },
            {
                "_id": 1,
            },
        ).to_list(length=100)
        conversation_document_ids = [
            str(document.get("_id"))
            for document in conversation_documents
            if document.get("_id") is not None
        ]

        if not conversation_document_ids:
            return []

        section_query["document_id"] = {
            "$in": conversation_document_ids,
        }

    sections = await document_sections_collection.find(section_query).sort([
        ("document_id", 1),
        ("sheet", 1),
        ("paragraph", 1),
        ("page", 1),
    ]).to_list(length=limit)

    if not sections:
        return []

    document_ids = list(dict.fromkeys(
        str(section.get("document_id"))
        for section in sections
        if section.get("document_id")
    ))
    object_ids = [
        object_id
        for object_id in (as_object_id(document_id) for document_id in document_ids)
        if object_id is not None
    ]
    documents = (
        await documents_collection.find({
            "_id": {
                "$in": object_ids,
            }
        }).to_list(length=len(object_ids))
        if object_ids
        else []
    )
    document_map = {
        str(document.get("_id")): document
        for document in documents
    }

    grouped_sections: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for section in sections:
        grouped_sections[str(section.get("document_id") or "")].append(section)

    extracted = []

    for document_id, grouped in grouped_sections.items():
        document = document_map.get(document_id) or {
            "_id": document_id,
            "conversation_id": conversation_id,
            "original_filename": "Document",
        }
        extracted.extend(FunctionCatalogExtractor.extract(
            sections=grouped,
            document=document,
        ))

    normalized_function_name = normalize_identifier(function_name)
    return [
        entry
        for entry in extracted
        if entry.get("normalized_function_name") == normalized_function_name
    ][:limit]


def merge_function_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "descriptions": [],
        "exceptions": [],
        "status_blocks": [],
        "examples": [],
        "entries": entries,
    }

    for entry in entries:
        if entry.get("description"):
            merged["descriptions"].append(entry["description"])

        for error_case in entry.get("exceptions") or []:
            merged["exceptions"].append(error_case)

        for status_block in entry.get("status_blocks") or []:
            merged["status_blocks"].append(status_block)

        for example in entry.get("examples") or []:
            merged["examples"].append(example)

    for key in ("descriptions", "exceptions", "examples"):
        merged[key] = list(dict.fromkeys(merged[key]))

    seen_status_blocks = set()
    unique_status_blocks = []

    for block in merged["status_blocks"]:
        identity = (
            block.get("title"),
            tuple(block.get("details") or []),
        )

        if identity in seen_status_blocks:
            continue

        seen_status_blocks.add(identity)
        unique_status_blocks.append(block)

    merged["status_blocks"] = unique_status_blocks

    return merged


def requested_function_topic(question: str) -> str:
    if EXCEPTIONS_QUERY_PATTERN.search(question or ""):
        return "exceptions"

    if EXAMPLE_QUERY_PATTERN.search(question or ""):
        return "example"

    return "overview"


def function_name_from_memory_resolution(
    *,
    question: str,
    memory_resolution: Any | None = None,
) -> str | None:
    for source in (
        getattr(memory_resolution, "explicit_entities", {}) if memory_resolution else {},
        getattr(memory_resolution, "inherited_entities", {}) if memory_resolution else {},
    ):
        if source.get("function_name"):
            return str(source["function_name"])

    return extract_function_name_from_question(question)


def build_function_reference_map(entries: list[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    source_ids: dict[str, str] = {}
    references = []

    for index, entry in enumerate(entries, start=1):
        source_id = f"F{index}"
        key = str(entry.get("_id") or index)
        source_ids[key] = source_id
        references.append(reference_from_entry(entry, source_id))

    return source_ids, deduplicate_references(references)


def section_from_items(
    *,
    title: str,
    intro: str,
    values: list[str],
    empty_message: str,
    source_ids: list[str],
) -> dict[str, Any]:
    if values:
        return {
            "title": title,
            "content": intro,
            "items": [
                {
                    "label": str(index),
                    "content": value,
                }
                for index, value in enumerate(values, start=1)
            ],
            "source_ids": source_ids,
        }

    return {
        "title": title,
        "content": empty_message,
        "source_ids": source_ids,
    }


def section_from_status_blocks(
    *,
    title: str,
    intro: str,
    blocks: list[dict[str, Any]],
    source_ids: list[str],
) -> dict[str, Any]:
    return {
        "title": title,
        "content": intro,
        "items": [
            {
                "label": str(block.get("title") or f"Statut {index}"),
                "content": "\n".join(
                    f"- {detail}"
                    for detail in block.get("details") or []
                    if detail
                ) or "Aucun detail supplementaire documente.",
            }
            for index, block in enumerate(blocks, start=1)
        ],
        "source_ids": source_ids,
    }


def status_blocks_section_for_function(
    *,
    function_name: str,
    blocks: list[dict[str, Any]],
    source_ids: list[str],
    title: str = "Codes retour / comportements documentes",
) -> dict[str, Any] | None:
    if not blocks:
        return None

    return section_from_status_blocks(
        title=title,
        intro=(
            f"Voici les statuts et comportements documentes pour la fonction "
            f"{function_name}. Les libelles sont conserves depuis la source."
        ),
        blocks=blocks,
        source_ids=source_ids,
    )


def has_exception_status_block(blocks: list[dict[str, Any]]) -> bool:
    return any(
        block.get("is_exception")
        and (
            block.get("details")
            or block.get("title")
        )
        for block in blocks
    )


async def answer_function_question(
    *,
    question: str,
    original_question: str | None = None,
    conversation_id: str | None = None,
    referenced_document_ids: list[str] | None = None,
    memory_resolution: Any | None = None,
) -> dict[str, Any] | None:
    function_name = function_name_from_memory_resolution(
        question=question,
        memory_resolution=memory_resolution,
    )

    if not function_name:
        return None

    entries = await find_function_catalog_entries(
        function_name=function_name,
        conversation_id=conversation_id,
        referenced_document_ids=referenced_document_ids,
    )

    if not entries:
        return None

    merged = merge_function_entries(entries)
    overview = merged_function_overview(entries, function_name)
    topic = requested_function_topic(f"{original_question or ''} {question}")
    source_id_map, references = build_function_reference_map(entries)
    source_ids = list(dict.fromkeys(
        source_id_map.get(str(entry.get("_id") or index), f"F{index}")
        for index, entry in enumerate(entries, start=1)
    ))
    sections: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    if topic == "exceptions":
        if merged["status_blocks"]:
            sections.append(section_from_status_blocks(
                title="Codes retour / exceptions documentes",
                intro=(
                    f"Voici les statuts documentes pour la fonction "
                    f"{function_name}, avec les details conserves depuis la "
                    "source."
                ),
                blocks=merged["status_blocks"],
                source_ids=source_ids,
            ))
        else:
            sections.append(section_from_items(
                title="Exceptions / erreurs documentees",
                intro=(
                    f"Voici les cas d'erreur ou exceptions explicitement associes "
                    f"a la fonction {function_name} dans les sources structurees."
                ),
                values=merged["exceptions"],
                empty_message=(
                    f"Les sources structurees disponibles pour {function_name} ne "
                    "contiennent pas d'exception explicite exploitable."
                ),
                source_ids=source_ids,
            ))

        if not merged["exceptions"] and not has_exception_status_block(merged["status_blocks"]):
            issues.append({
                "severity": "warning",
                "title": "Aucune exception explicite trouvee",
                "detail": (
                    "Le catalogue de fonctions n'a trouve aucun cas d'erreur "
                    "clairement rattache a cette fonction."
                ),
            })

        summary = (
            f"La fonction {function_name} a ete retrouvee dans la documentation. "
            "Les exceptions ci-dessous proviennent uniquement des lignes ou "
            "passages structures rattaches a cette fonction."
        )

    elif topic == "example":
        sections.append(section_from_items(
            title="Exemple documente",
            intro=(
                f"Exemples ou traces d'utilisation detectes pour la fonction "
                f"{function_name}."
            ),
            values=merged["examples"],
            empty_message=(
                f"Aucun exemple concret explicite n'a ete trouve pour "
                f"{function_name} dans les sources structurees disponibles."
            ),
            source_ids=source_ids,
        ))

        if not merged["examples"]:
            issues.append({
                "severity": "warning",
                "title": "Aucun exemple explicite trouve",
                "detail": (
                    "La reponse ne fabrique pas d'exemple hors documentation."
                ),
            })

        summary = (
            f"La fonction {function_name} est documentee, mais les exemples "
            "concrets disponibles dependent des lignes extraites du document."
        )

    else:
        sections.append({
            "title": "Role",
            "content": overview["description"],
            "source_ids": source_ids,
        })

        metadata = overview.get("metadata") or {}

        if metadata:
            sections.append({
                "title": "Informations techniques",
                "content": "Elements techniques identifies dans la source.",
                "items": [
                    {
                        "label": "Bibliotheque",
                        "content": metadata["library"],
                    }
                    for key in ("library",)
                    if key in metadata
                ] + [
                    {
                        "label": "Chemin source",
                        "content": metadata["source_path"],
                    }
                    for key in ("source_path",)
                    if key in metadata
                ],
                "source_ids": source_ids,
            })

        status_section = status_blocks_section_for_function(
            function_name=function_name,
            blocks=merged["status_blocks"],
            source_ids=source_ids,
        )

        if status_section:
            sections.append(status_section)
        elif merged["exceptions"]:
            sections.append(section_from_items(
                title="Exceptions / erreurs documentees",
                intro=(
                    f"Voici les cas d'erreur ou exceptions explicitement "
                    f"associes a la fonction {function_name}."
                ),
                values=merged["exceptions"],
                empty_message="",
                source_ids=source_ids,
            ))

        summary = (
            f"La fonction {function_name} a ete retrouvee dans le catalogue "
            "documentaire extrait des fichiers fournis."
        )

    return {
        "summary": summary,
        "sections": sections,
        "story": [],
        "issues": issues,
        "recommendations": [],
        "references": references,
    }
