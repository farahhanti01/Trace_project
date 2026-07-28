import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from bson import ObjectId
from fastapi import HTTPException

from app.database import (
    document_sections_collection,
    documents_collection,
)
from app.services.documentation_agent_service import (
    build_context,
    reference_from_section,
)
from app.services.hps_ai_service import (
    HpsAiConfigurationError,
    HpsAiRequestError,
    call_hps_ai,
)
from app.services.log_parser_service import (
    build_statistics,
    observed_facts_for_transaction,
    parse_log_transactions,
    transaction_status,
)
from app.services.retrieval_service import (
    extract_relevant_excerpt,
    select_relevant_sections,
)


MAX_PDF_REFERENCE_SECTIONS = 4
MAX_HSM_REFERENCE_SECTIONS = 5
MAX_LLM_TRANSACTIONS = 8
MAX_RESPONSE_TRANSACTIONS = 30
MAX_DOCUMENTATION_FINDINGS = 3
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOG_ANALYSIS_USE_LLM = os.getenv(
    "LOG_ANALYSIS_USE_LLM",
    "false",
).lower() in {"1", "true", "yes"}
FIELD_ALIASES = {
    "rrn": "037",
    "retrieval reference": "037",
    "retrieval reference number": "037",
    "response code": "039",
    "action code": "039",
    "card": "002",
    "carte": "002",
}
FIELD_039_REQUIRED_RESPONSE_MTIS = {
    "0110",
    "0130",
    "0310",
    "0312",
    "0410",
    "0430",
}
IGNORED_QUERY_FUNCTIONS = {
    "dumpvisa",
}
TOTAL_ANALYSIS_TERMS = (
    "analyse total",
    "analyse totale",
    "analyse complete",
    "analyse globale",
    "tout analyser",
    "toute la trace",
    "all analysis",
    "full analysis",
    "complete analysis",
)
HSM_ANALYSIS_TERMS = (
    "hsm",
    "hsmmresultcode",
    "hsmresultcode",
    "command_",
    "to hsm",
    "from hsm",
    "traitement hsm",
    "traitements hsm",
)
LOG_STORY_TERMS = (
    "log story",
    "logstory",
    "fonctions",
    "fonction",
    "ordre d'apparition",
    "ordre apparition",
)


async def load_sections_for_documents(
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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
            ("chunk_index", 1),
        ]
    )

    sections = await cursor.to_list(length=5_000)
    enriched_sections = []

    for section in sections:
        document = documents_by_id.get(section["document_id"])

        if document is None:
            continue

        enriched_sections.append({
            "document_id": section.get("document_id"),
            "source": document["original_filename"],
            "extension": document.get("extension"),
            "text": section.get("text", ""),
            "page": section.get("page"),
            "sheet": section.get("sheet"),
            "paragraph": section.get("paragraph"),
            "heading": section.get("heading"),
            "section_index": section.get("section_index"),
            "chunk_index": section.get("chunk_index"),
            "embedding": section.get("embedding"),
            "agent": document.get("agent"),
        })

    return enriched_sections


async def load_log_sections(
    conversation_id: str,
) -> list[dict[str, Any]]:
    documents = await documents_collection.find(
        {
            "conversation_id": conversation_id,
            "agent": "log",
            "status": "extracted",
            "extension": {"$in": [".txt", ".log"]},
        }
    ).sort(
        "created_at",
        1,
    ).to_list(length=50)

    return await load_sections_for_documents(documents)


async def load_log_texts(
    conversation_id: str,
) -> dict[str, str]:
    documents = await documents_collection.find(
        {
            "conversation_id": conversation_id,
            "agent": "log",
            "status": "extracted",
            "extension": {"$in": [".txt", ".log"]},
        }
    ).sort(
        "created_at",
        1,
    ).to_list(length=50)

    texts = {}

    for document in documents:
        file_path = BACKEND_ROOT / document["relative_path"]

        if not file_path.exists():
            continue

        try:
            texts[document["original_filename"]] = file_path.read_text(
                encoding=document.get("encoding") or "utf-8",
            )
        except UnicodeDecodeError:
            texts[document["original_filename"]] = file_path.read_text(
                encoding="latin-1",
            )

    return texts


async def load_reference_sections(
    conversation_id: str,
    referenced_document_ids: list[str],
) -> list[dict[str, Any]]:
    object_ids = [
        ObjectId(document_id)
        for document_id in referenced_document_ids
        if ObjectId.is_valid(document_id)
    ]

    if object_ids:
        query = {
            "_id": {"$in": object_ids},
            "status": "extracted",
            "extension": {"$in": [".pdf", ".xlsx"]},
        }
    else:
        query = {
            "conversation_id": conversation_id,
            "status": "extracted",
            "extension": {"$in": [".pdf", ".xlsx"]},
        }

    documents = await documents_collection.find(query).to_list(length=100)

    return await load_sections_for_documents(documents)


def group_log_texts_by_source(
    log_sections: list[dict[str, Any]],
) -> dict[str, str]:
    grouped_lines: dict[str, list[str]] = {}

    for section in sorted(
        log_sections,
        key=lambda item: (
            item.get("source") or "",
            item.get("section_index") or 0,
            item.get("chunk_index") or 0,
        ),
    ):
        grouped_lines.setdefault(section["source"], []).append(
            section.get("text", "")
        )

    return {
        source: "\n".join(parts)
        for source, parts in grouped_lines.items()
    }


def normalize_question_text(
    question: str,
) -> str:
    ascii_question = unicodedata.normalize(
        "NFKD",
        question,
    ).encode(
        "ascii",
        "ignore",
    ).decode("ascii")

    return ascii_question.lower().replace("fld", "field")


def extract_query_constraints(
    question: str,
) -> dict[str, Any]:
    normalized = normalize_question_text(question)
    constraints: dict[str, Any] = {
        "fields": {},
        "mti": None,
        "log_index": None,
        "functions": [],
        "errors": [],
        "hsm_result_codes": [],
        "hsm_threads": [],
        "hsm_commands": [],
        "has_constraints": False,
    }

    for match in re.finditer(
        r"\b(?:field|champ)\s*0?(002|003|037|039)\b"
        r"(?:\s+(?:ayant|avec|value|valeur|equals|egal|egale|=|is))*"
        r"[^0-9A-Za-z]{0,20}"
        r"([0-9*]{2,30})",
        normalized,
    ):
        constraints["fields"][match.group(1)] = match.group(2)

    for alias, field in FIELD_ALIASES.items():
        alias_match = re.search(
            rf"\b{re.escape(alias)}\b[^0-9A-Za-z]{{0,20}}([0-9*]{{2,30}})",
            normalized,
        )

        if alias_match:
            constraints["fields"][field] = alias_match.group(1)

    mti_match = re.search(
        r"\bmti\b[^0-9]{0,10}([0-9]{4})\b",
        normalized,
    )

    if mti_match:
        constraints["mti"] = mti_match.group(1)

    block_match = re.search(
        r"\b(?:bloc|block|transaction)\s*#?\s*([0-9]{1,6})\b",
        normalized,
    )

    if block_match:
        constraints["log_index"] = int(block_match.group(1))

    constraints["hsm_threads"] = [
        match.group(1)
        for match in re.finditer(
            r"\bthread\b[^0-9]{0,10}([0-9]{4,20})\b",
            normalized,
        )
    ]

    hsm_result_codes = {
        match.group(1).upper()
        for match in re.finditer(
            r"\b(?:hsmresultcode|code\s+hsm|retour\s+hsm)"
            r"[^A-Za-z0-9]{0,20}([A-Z]{2}[A-Z0-9]{2})\b",
            question,
            flags=re.IGNORECASE,
        )
    }
    if "hsm" in normalized:
        hsm_result_codes.update(
            match.group(1).upper()
            for match in re.finditer(
                r"\b([A-Z]{2}[0-9]{2})\b",
                question,
                flags=re.IGNORECASE,
            )
        )
    constraints["hsm_result_codes"] = list(hsm_result_codes)

    constraints["hsm_commands"] = list({
        match.group(1).upper()
        for match in re.finditer(
            r"\b(?:command|commande|command_)\s*_?\s*([A-Z0-9]{2,4})\b",
            question,
            flags=re.IGNORECASE,
        )
    })

    constraints["functions"] = [
        match.group(0)
        for match in re.finditer(
            r"\b[A-Z][A-Za-z0-9_]{2,}\b",
            question,
        )
        if (
            match.group(0).upper()
            not in {"NOK", "OK", "ERROR", "ERR", "KO", "WARNING"}
            and normalize_function_name(match.group(0))
            not in IGNORED_QUERY_FUNCTIONS
            and (
                "(" in question[match.end():match.end() + 2]
                or match.group(0).lower().startswith(
                    ("get", "check", "auth")
                )
            )
        )
    ]

    constraints["errors"] = [
        match.group(0).upper()
        for match in re.finditer(
            r"\b(?:NOK|ERROR|ERR|KO|WARNING)\b\s*(?:\(\s*-?[0-9]+\s*\))?",
            question,
            flags=re.IGNORECASE,
        )
    ]

    constraints["has_constraints"] = bool(
        constraints["fields"]
        or constraints["mti"]
        or constraints["log_index"]
        or constraints["functions"]
        or constraints["errors"]
        or constraints["hsm_result_codes"]
        or constraints["hsm_threads"]
        or constraints["hsm_commands"]
    )

    return constraints


def display_options_for_question(
    question: str,
) -> dict[str, bool]:
    normalized = normalize_question_text(question)
    wants_total = any(term in normalized for term in TOTAL_ANALYSIS_TERMS)
    wants_hsm = any(term in normalized for term in HSM_ANALYSIS_TERMS)
    wants_log_story = any(term in normalized for term in LOG_STORY_TERMS)
    only_hsm = (
        wants_hsm
        and not wants_log_story
        and re.search(r"\b(?:uniquement|seulement|only)\b", normalized)
    )
    only_log_story = (
        wants_log_story
        and not wants_hsm
        and re.search(r"\b(?:uniquement|seulement|only)\b", normalized)
    )

    if wants_total:
        return {
            "show_fields": True,
            "show_log_story": True,
            "show_hsm": True,
            "show_documentation_findings": True,
        }

    if only_hsm:
        return {
            "show_fields": True,
            "show_log_story": False,
            "show_hsm": True,
            "show_documentation_findings": True,
        }

    if only_log_story:
        return {
            "show_fields": True,
            "show_log_story": True,
            "show_hsm": False,
            "show_documentation_findings": True,
        }

    return {
        "show_fields": True,
        "show_log_story": True,
        "show_hsm": True,
        "show_documentation_findings": True,
    }


def masked_value_matches(
    observed: str | None,
    requested: str,
) -> bool:
    if not observed:
        return False

    observed_digits = re.sub(r"\D", "", observed)
    requested_digits = re.sub(r"\D", "", requested)

    if "*" in requested:
        pattern = re.escape(requested).replace(r"\*", ".*")
        return re.fullmatch(pattern, observed or "") is not None

    if "*" in observed:
        return (
            requested_digits.startswith(observed_digits[:6])
            and requested_digits.endswith(observed_digits[-4:])
        )

    return observed_digits == requested_digits


def transaction_matches_constraints(
    transaction: dict[str, Any],
    constraints: dict[str, Any],
) -> bool:
    fields = transaction.get("fields", {})

    for field, requested_value in constraints.get("fields", {}).items():
        if not masked_value_matches(
            fields.get(field),
            requested_value,
        ):
            return False

    if constraints.get("mti") and transaction.get("mti") != constraints["mti"]:
        return False

    if (
        constraints.get("log_index") is not None
        and transaction.get("log_index") != constraints["log_index"]
    ):
        return False

    requested_functions = [
        normalize_function_name(name)
        for name in constraints.get("functions", [])
    ]

    if requested_functions:
        story_functions = {
            normalize_function_name(item.get("function_name", ""))
            for item in transaction.get("log_story", [])
        }

        if not any(
            function in story_functions
            for function in requested_functions
        ):
            return False

    requested_errors = [
        error.replace(" ", "")
        for error in constraints.get("errors", [])
    ]

    if requested_errors:
        observed_errors = [
            str(item.get("detected_error", "")).upper().replace(" ", "")
            for item in transaction.get("log_story", [])
        ]
        observed_errors.extend(
            str(command.get("hsm_result_code", "")).upper().replace(" ", "")
            for command in (transaction.get("hsm_analysis") or {}).get(
                "commands",
                [],
            )
        )
        observed_errors.extend(
            str(command.get("detected_status", "")).upper().replace(" ", "")
            for command in (transaction.get("hsm_analysis") or {}).get(
                "commands",
                [],
            )
        )

        if not any(
            requested in observed
            for requested in requested_errors
            for observed in observed_errors
        ):
                return False

    hsm_analysis = transaction.get("hsm_analysis") or {}
    hsm_commands = hsm_analysis.get("commands", [])

    if constraints.get("hsm_threads"):
        observed_threads = {
            str(hsm_analysis.get("thread") or ""),
            *{
                str(command.get("thread") or "")
                for command in hsm_commands
            },
        }

        if not any(
            requested_thread in observed_threads
            for requested_thread in constraints["hsm_threads"]
        ):
            return False

    if constraints.get("hsm_result_codes"):
        observed_codes = {
            str(command.get("hsm_result_code") or "").upper()
            for command in hsm_commands
        }

        if not any(
            requested_code in observed_codes
            for requested_code in constraints["hsm_result_codes"]
        ):
            return False

    if constraints.get("hsm_commands"):
        observed_commands = {
            str(command.get("command") or "").upper()
            for command in hsm_commands
        }
        observed_commands.update(
            str(command.get("response_command") or "").upper()
            for command in hsm_commands
        )

        if not any(
            requested_command in observed_commands
            for requested_command in constraints["hsm_commands"]
        ):
            return False

    return True


def select_transactions_for_question(
    transactions: list[dict[str, Any]],
    question: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    constraints = extract_query_constraints(question)

    if not constraints["has_constraints"]:
        return response_transactions(
            transactions=transactions,
            question=question,
        ), constraints

    matched_transactions = [
        transaction
        for transaction in transactions
        if transaction_matches_constraints(
            transaction=transaction,
            constraints=constraints,
        )
    ]

    return matched_transactions[:MAX_RESPONSE_TRANSACTIONS], constraints


def parse_xlsx_row(
    section: dict[str, Any],
) -> dict[str, Any]:
    parts = [
        part.strip()
        for part in section.get("text", "").split("|", 4)
    ]

    return {
        "function": parts[0] if len(parts) > 0 else "",
        "source": parts[1] if len(parts) > 1 else "",
        "path": parts[2] if len(parts) > 2 else "",
        "description": parts[3] if len(parts) > 3 else "",
        "exception": parts[4] if len(parts) > 4 else "",
        "return_code": find_return_code(section.get("text", "")),
        "control_rule": find_control_rule(section.get("text", "")),
        "excel_reference": {
            "source": section.get("source"),
            "sheet": section.get("sheet"),
            "row": section.get("paragraph"),
        },
    }


def normalize_function_name(
    name: str,
) -> str:
    return re.sub(r"\(\s*\)$", "", name.strip()).lower()


def documented_function_names(
    xlsx_sections: list[dict[str, Any]],
) -> set[str]:
    names = set()

    for section in xlsx_sections:
        function_name = parse_xlsx_row(section).get("function", "")

        if function_name:
            names.add(normalize_function_name(function_name))

    return names


def find_return_code(
    text: str,
) -> str:
    match = re.search(
        r"\b(?:return code|code retour|rc)\s*[:=]\s*([^|;]+)",
        text,
        flags=re.IGNORECASE,
    )

    return match.group(1).strip() if match else ""


def find_control_rule(
    text: str,
) -> str:
    match = re.search(
        r"\b(?:regle|rÃ¨gle|control rule|rule)\s*[:=]\s*([^|;]+)",
        text,
        flags=re.IGNORECASE,
    )

    return match.group(1).strip() if match else ""


def find_excel_documentation(
    function_name: str,
    xlsx_sections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized_function = normalize_function_name(function_name)

    for section in xlsx_sections:
        row = parse_xlsx_row(section)

        if normalize_function_name(row["function"]) == normalized_function:
            return row

    for section in xlsx_sections:
        text = section.get("text", "").lower()

        if normalized_function in text:
            return parse_xlsx_row(section)

    return None


def compact_text(
    text: str,
    limit: int = 1000,
) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()

    if len(normalized) <= limit:
        return normalized

    return f"{normalized[:limit].rstrip()}..."


def split_exception_candidates(
    text: str,
) -> list[str]:
    normalized = re.sub(r"\s+", " ", text or "").strip()

    if not normalized:
        return []

    error_code_pattern = (
        r"(?:"
        r"\(\s*-\s*\d+\s*\)"
        r"|\(\s*-?\d+\s*\)"
        r"|-?\s*\d+"
        r"|\([^)]*code\s*:\s*-?\s*\d+[^)]*\)"
        r")"
    )
    marker_pattern = re.compile(
        r"(?:^|\s)(?=(?:NOK|OK|ERROR|ERR|KO|SYSTEM_MALFUNCTION)"
        rf"\s*(?:{error_code_pattern})?"
        r"\s*[:=-])",
        flags=re.IGNORECASE,
    )
    matches = list(marker_pattern.finditer(normalized))

    if matches:
        candidates = []

        for index, match in enumerate(matches):
            start = match.start()
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(normalized)
            )
            candidate = normalized[start:end].strip(" -;")

            if candidate:
                candidates.append(candidate)

        return candidates

    candidates = re.split(
        rf"\s+(?=(?:NOK|OK|ERROR|ERR|KO)\s*(?:{error_code_pattern})?\s*[:=-])",
        normalized,
        flags=re.IGNORECASE,
    )

    if len(candidates) > 1:
        return [candidate.strip(" -;") for candidate in candidates if candidate.strip()]

    return [
        part.strip(" -;")
        for part in re.split(r"\s+-\s+", normalized)
        if part.strip()
    ] or [normalized]


def exception_matches_error(
    candidate: str,
    detected_error: str,
) -> bool:
    if not detected_error:
        return False

    normalized_candidate = candidate.lower()
    normalized_error = detected_error.lower()
    code_match = re.search(r"-?\d+", detected_error)
    compact_candidate = re.sub(r"\s+", "", normalized_candidate)
    compact_error = re.sub(r"\s+", "", normalized_error)

    if normalized_error in normalized_candidate:
        return True

    if compact_error in compact_candidate:
        return True

    if code_match and code_match.group(0) in normalized_candidate:
        label = detected_error.split()[0].lower()
        return label in normalized_candidate

    if code_match and code_match.group(0) in compact_candidate:
        label = detected_error.split()[0].lower()
        return label in normalized_candidate

    return False


def select_matching_exception(
    exception_text: str,
    detected_error: str,
) -> str:
    candidates = split_exception_candidates(exception_text)

    for candidate in candidates:
        if exception_matches_error(candidate, detected_error):
            return compact_text(candidate)

    return compact_text(candidates[0] if candidates else exception_text)


def filter_documented_log_story(
    transaction: dict[str, Any],
    documented_names: set[str],
) -> None:
    if not documented_names:
        return

    filtered_story = [
        item
        for item in transaction.get("log_story", [])
        if normalize_function_name(item.get("function_name", ""))
        in documented_names
    ]

    for order, item in enumerate(filtered_story, start=1):
        item["order"] = order

    transaction["log_story"] = filtered_story


def build_error_payload(
    function_name: str,
    xlsx_doc: dict[str, Any] | None,
    detected_error: str = "",
    pdf_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    xlsx_doc = xlsx_doc or {}

    return {
        "detected_error": detected_error,
        "exception": select_matching_exception(
            xlsx_doc.get("exception", ""),
            detected_error,
        ),
        "description": compact_text(xlsx_doc.get("description", "")),
        "path": xlsx_doc.get("path", ""),
        "function": xlsx_doc.get("function") or function_name,
        "excel_reference": xlsx_doc.get("excel_reference", {}),
        "pdf_reference": pdf_reference or {},
    }


def reference_key(
    reference: dict[str, Any],
) -> tuple[Any, Any, Any, Any]:
    return (
        reference.get("source"),
        reference.get("page"),
        reference.get("sheet"),
        reference.get("paragraph"),
    )


def append_unique_reference(
    references: list[dict[str, Any]],
    reference: dict[str, Any],
) -> None:
    if not reference:
        return

    if reference.get("row") and not reference.get("paragraph"):
        reference = {
            **reference,
            "paragraph": reference["row"],
        }

    existing_keys = {
        reference_key(item)
        for item in references
    }

    if reference_key(reference) not in existing_keys:
        references.append(reference)


def observed_pdf_anomalies(
    transaction: dict[str, Any],
) -> list[dict[str, str]]:
    fields = transaction.get("fields", {})
    mti = transaction.get("mti")
    anomalies = []

    if not transaction.get("mti"):
        anomalies.append({
            "type": "missing_mti",
            "anomaly": "MTI is missing from the transaction block.",
            "observed_value": "missing",
            "query_terms": "MTI message type authorization request response",
        })

    for field in ("002", "003", "037"):
        if not fields.get(field):
            anomalies.append({
                "type": f"missing_field_{field}",
                "anomaly": f"FLD {field} is missing from the transaction.",
                "observed_value": "missing",
                "query_terms": f"Field {field} authorization message required",
            })

    if mti in FIELD_039_REQUIRED_RESPONSE_MTIS and not fields.get("039"):
        anomalies.append({
            "type": "missing_response_field_039",
            "anomaly": (
                f"FLD 039 is missing from response MTI {mti}."
            ),
            "observed_value": "missing",
            "query_terms": (
                "Field 39 is required in all 0110 0130 0310 0312 "
                "0410 0430 responses reject code 0294 Field missing"
            ),
        })

    if (
        mti in FIELD_039_REQUIRED_RESPONSE_MTIS
        and fields.get("039")
        and fields.get("039") != "00"
    ):
        anomalies.append({
            "type": "response_code_failure",
            "anomaly": "FLD 039 indicates a non-approved authorization response.",
            "observed_value": fields["039"],
            "query_terms": (
                f"Field 039 response code {fields['039']} "
                "authorization response error decline"
            ),
        })

    return anomalies


def pdf_section_supports_anomaly(
    section: dict[str, Any],
    anomaly: dict[str, str],
) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            section.get("heading"),
            section.get("text"),
        )
    ).lower()
    anomaly_type = anomaly.get("type", "")

    if anomaly_type == "missing_response_field_039":
        return (
            "field 39 is required" in text
            or "0294 = field missing" in text
            or (
                "field 39" in text
                and "required in all" in text
                and "0110" in text
            )
        )

    if anomaly_type == "response_code_failure":
        return (
            "field 039" in text
            or "response code" in text
            or "action code" in text
            or "authorization response" in text
        )

    if anomaly_type == "missing_mti":
        return (
            "mti" in text
            or "message type" in text
            or "message identification" in text
        )

    field_match = re.search(r"missing_field_(\d+)", anomaly_type)

    if field_match:
        field = field_match.group(1)
        return (
            f"field {int(field)}" in text
            or f"field {field}" in text
            or f"fld {field}" in text
        )

    return False


def build_pdf_documentation_findings(
    transaction: dict[str, Any],
    selected_pdf_sections: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    findings = []
    anomalies = observed_pdf_anomalies(transaction)

    if not anomalies:
        return []

    for anomaly in anomalies:
        for section in selected_pdf_sections:
            if not pdf_section_supports_anomaly(section, anomaly):
                continue

            excerpt = extract_relevant_excerpt(
                question=f"{query} {anomaly.get('query_terms', '')}",
                text=section.get("text", ""),
            )
            reference = reference_from_section(section)

            findings.append({
                "source_type": "pdf",
                "type": "anomaly_justification",
                "anomaly": anomaly["anomaly"],
                "observed_value": anomaly["observed_value"],
                "expected_rule": excerpt,
                "explanation": (
                    "The observed value is treated as an anomaly because "
                    "the referenced PDF section provides the applicable "
                    "message or field rule."
                ),
                **reference,
                "heading": section.get("heading"),
                "evidence": excerpt,
            })
            break

        if len(findings) >= MAX_DOCUMENTATION_FINDINGS:
            break

    return findings


def hsm_terms_for_transaction(
    transaction: dict[str, Any],
) -> str:
    hsm_analysis = transaction.get("hsm_analysis") or {}
    terms = [
        "HSM command response result code",
        "Core Host Commands",
        "PIN Verification",
        "Visa PVV",
    ]

    for command in hsm_analysis.get("commands", []):
        for key in ("command", "response_command", "hsm_result_code"):
            value = command.get(key)

            if value:
                terms.append(str(value))

    return " ".join(terms)


def hsm_command_query(
    command: dict[str, Any],
) -> str:
    command_code = command.get("command") or ""
    response_command = command.get("response_command") or ""
    result_code = command.get("hsm_result_code") or ""
    return_code = command.get("return_code") or result_code[2:]

    return " ".join(
        value
        for value in [
            f"{command_code} command",
            f'"{command_code}" command' if command_code else "",
            f"{response_command} response",
            f'"{response_command}" response' if response_command else "",
            f"{command_code} {response_command}",
            result_code,
            f"response code {return_code}" if return_code else "",
            f"{command_code} HSM",
            "PIN Verification",
            "Visa PVV",
            "Core Host Commands",
        ]
        if value.strip()
    )


def pdf_section_supports_hsm_command(
    section: dict[str, Any],
    command: dict[str, Any],
) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            section.get("heading"),
            section.get("text"),
        )
    ).lower()
    result_code = str(command.get("hsm_result_code") or "").lower()
    return_code = str(command.get("return_code") or "").lower()
    command_code = str(command.get("command") or "").lower()
    response_command = str(command.get("response_command") or "").lower()

    if result_code and result_code in text:
        return True

    if (
        return_code
        and response_command
        and response_command in text
        and re.search(rf"\b{re.escape(return_code)}\b", text)
    ):
        return True

    if (
        command_code
        and response_command
        and command_code in text
        and response_command in text
        and "hsm" in text
    ):
        return True

    return False


def documented_hsm_value(
    value: str | None,
) -> str:
    return value or "Non trouve dans la documentation fournie"


async def build_hsm_documentation_findings(
    transaction: dict[str, Any],
    pdf_sections: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    hsm_analysis = transaction.get("hsm_analysis") or {}
    findings = []

    for command in hsm_analysis.get("commands", []):
        if not command.get("hsm_result_code"):
            continue

        command_query = " ".join([
            query,
            hsm_command_query(command),
        ])
        selected_pdf_sections = (
            await select_relevant_sections(
                question=command_query,
                sections=pdf_sections,
                limit=MAX_HSM_REFERENCE_SECTIONS,
                use_embeddings=False,
            )
            if pdf_sections
            else []
        )
        command_findings = []

        for section in selected_pdf_sections:
            if not pdf_section_supports_hsm_command(section, command):
                continue

            excerpt = extract_relevant_excerpt(
                question=command_query,
                text=section.get("text", ""),
            )
            reference = reference_from_section(section)

            finding = {
                "source_type": "pdf",
                "type": "hsm_code_explanation",
                "title": (
                    f"HSM result {command.get('hsm_result_code')} "
                    f"for {command.get('command') or 'command'}"
                ),
                "command": command.get("command"),
                "response_command": command.get("response_command"),
                "hsm_result_code": command.get("hsm_result_code"),
                "return_code": command.get("return_code"),
                "thread": command.get("thread") or hsm_analysis.get("thread"),
                "explanation": excerpt,
                **reference,
                "heading": section.get("heading"),
                "evidence": excerpt,
            }
            command_findings.append(finding)
            findings.append(finding)

            if len(command_findings) >= 2:
                break

        primary_finding = command_findings[0] if command_findings else None
        primary_explanation = (
            primary_finding.get("explanation")
            if primary_finding
            else ""
        )
        primary_heading = (
            primary_finding.get("heading")
            if primary_finding
            else ""
        )

        command["command_name"] = documented_hsm_value(primary_heading)
        command["command_description"] = documented_hsm_value(
            primary_explanation
        )
        command["response_name"] = documented_hsm_value(
            primary_heading
            if command.get("response_command")
            else ""
        )
        command["return_code_meaning"] = documented_hsm_value(
            primary_explanation
        )
        command["functional_result"] = (
            "Non determine: aucune explication documentaire trouvee."
            if not primary_finding
            else (
                "SUCCESS"
                if command.get("status") == "SUCCESS"
                else "FAILED"
                if command.get("status") == "FAILED"
                else command.get("status") or "UNKNOWN"
            )
        )
        command["technical_interpretation"] = documented_hsm_value(
            primary_explanation
        )
        command["documentation_findings"] = command_findings

    return findings[:MAX_DOCUMENTATION_FINDINGS]


async def enrich_transactions_with_documentation(
    transactions: list[dict[str, Any]],
    reference_sections: list[dict[str, Any]],
) -> None:
    xlsx_sections = [
        section
        for section in reference_sections
        if str(section.get("source", "")).lower().endswith(".xlsx")
    ]
    pdf_sections = [
        section
        for section in reference_sections
        if str(section.get("source", "")).lower().endswith(".pdf")
    ]
    documented_names = documented_function_names(xlsx_sections)

    for transaction in transactions:
        filter_documented_log_story(
            transaction=transaction,
            documented_names=documented_names,
        )
        transaction["status"] = transaction_status(
            fields=transaction.get("fields", {}),
            log_story=transaction.get("log_story", []),
            hsm_analysis=transaction.get("hsm_analysis"),
        )
        transaction["observed_facts"] = observed_facts_for_transaction(
            transaction
        )

        if not should_enrich_transaction(transaction):
            continue

        query = build_transaction_query(transaction)
        selected_pdf_sections = (
            await select_relevant_sections(
                question=query,
                sections=pdf_sections,
                limit=MAX_PDF_REFERENCE_SECTIONS,
                use_embeddings=False,
            )
            if pdf_sections
            else []
        )
        transaction["sources"] = [
            reference_from_section(section)
            for section in selected_pdf_sections
        ]
        transaction["documentation_findings"] = (
            build_pdf_documentation_findings(
                transaction=transaction,
                selected_pdf_sections=selected_pdf_sections,
                query=query,
            )
        )
        hsm_findings = await build_hsm_documentation_findings(
            transaction=transaction,
            pdf_sections=pdf_sections,
            query=query,
        )
        if transaction.get("hsm_analysis"):
            transaction["hsm_analysis"]["documentation_findings"] = (
                hsm_findings
            )
        transaction["evidence"].extend([
            {
                **reference_from_section(section),
                "heading": section.get("heading"),
                "excerpt": extract_relevant_excerpt(
                    question=query,
                    text=section.get("text", ""),
                ),
            }
            for section in selected_pdf_sections
        ])

        for story_item in transaction["log_story"]:
            function_name = story_item.get("function_name", "")

            if not function_name:
                continue

            xlsx_doc = find_excel_documentation(
                function_name=function_name,
                xlsx_sections=xlsx_sections,
            )

            if xlsx_doc:
                append_unique_reference(
                    transaction["sources"],
                    xlsx_doc.get("excel_reference", {}),
                )

            if story_item.get("status") == "ERROR":
                story_item["error"] = build_error_payload(
                    function_name=function_name,
                    xlsx_doc=xlsx_doc,
                    detected_error=story_item.get("detected_error", ""),
                    pdf_reference=(
                        transaction["sources"][0]
                        if transaction["sources"]
                        else None
                    ),
                )

        transaction["documentation_findings"] = transaction[
            "documentation_findings"
        ][:MAX_DOCUMENTATION_FINDINGS]


def should_enrich_transaction(
    transaction: dict[str, Any],
) -> bool:
    if transaction.get("status") in {"FAILED", "WARNING"}:
        return True

    if (transaction.get("hsm_analysis") or {}).get("commands"):
        return True

    return any(
        item.get("status") in {"ERROR", "WARNING"}
        for item in transaction.get("log_story", [])
    )


def build_transaction_query(
    transaction: dict[str, Any],
) -> str:
    fields = transaction.get("fields", {})
    mti = transaction.get("mti")
    error_functions = [
        item.get("function_name", "")
        for item in transaction.get("log_story", [])
        if item.get("status") == "ERROR"
    ]
    field_039_context = (
        "Field 39 required in all 0110 0130 0310 0312 0410 0430 "
        "responses reject code 0294 Field missing"
        if mti in FIELD_039_REQUIRED_RESPONSE_MTIS
        and not fields.get("039")
        else (
            f"Field 039 {fields.get('039')}"
            if fields.get("039")
            else ""
        )
    )

    return " ".join(
        value
        for value in [
            f"MTI {mti or ''}",
            field_039_context,
            f"Field 003 {fields.get('003') or ''}",
            hsm_terms_for_transaction(transaction),
            " ".join(error_functions),
            "response code authorization transaction rule error",
        ]
        if value.strip()
    )


def deterministic_summary(
    transactions: list[dict[str, Any]],
    statistics: dict[str, int],
) -> str:
    has_hsm_only = any(
        not transaction.get("mti")
        and (transaction.get("hsm_analysis") or {}).get("commands")
        for transaction in transactions
    )
    split_method = (
        "Start DumpVisa()/Start DumpCis() et, si absent, par thread HSM"
        if has_hsm_only
        else "Start DumpVisa()/Start DumpCis()"
    )

    return (
        f"{statistics['total_transactions']} transaction(s) detectee(s): "
        f"{statistics['successful_transactions']} succes, "
        f"{statistics['failed_transactions']} echec(s), "
        f"{statistics['warning_transactions']} warning(s). "
        f"L'analyse deterministe a separe les transactions par {split_method}, "
        "extrait les champs ISO demandes, les traitements HSM et reconstruit "
        "la Log Story."
    )


def apply_deterministic_enrichment(
    transactions: list[dict[str, Any]],
) -> None:
    for transaction in transactions:
        fields = transaction.get("fields", {})
        error_functions = [
            item.get("function_name")
            for item in transaction.get("log_story", [])
            if item.get("status") == "ERROR"
        ]
        facts = [
            f"MTI detecte: {transaction.get('mti') or 'UNKNOWN'}.",
            f"Type message: {transaction.get('message_type') or 'Transaction'}.",
            f"Bloc log: #{transaction.get('log_index') or 'N/A'}.",
            f"Field 037 detecte: {fields.get('037') or 'non present'}.",
        ]

        if fields.get("039"):
            facts.append(f"Field 039 detecte: {fields['039']}.")

        hsm_analysis = transaction.get("hsm_analysis") or {}
        hsm_thread = hsm_analysis.get("thread")
        hsm_codes = hsm_analysis.get("result_codes") or []
        failing_hsm_codes = [
            command.get("hsm_result_code")
            for command in hsm_analysis.get("commands", [])
            if command.get("status") in {
                "FAILED",
                "TECHNICAL_ERROR",
                "FORMAT_ERROR",
                "COMMUNICATION_ERROR",
            }
            and command.get("hsm_result_code")
        ]

        if hsm_thread:
            facts.append(f"Thread HSM detecte: {hsm_thread}.")

        if hsm_codes:
            facts.append(
                "HsmResultCode detecte: "
                f"{', '.join(hsm_codes)}."
            )

        if error_functions:
            facts.append(
                "Fonctions documentees en erreur: "
                f"{', '.join(error_functions)}."
            )

        transaction["observed_facts"] = facts

        if not should_enrich_transaction(transaction):
            continue

        if error_functions:
            transaction["probable_cause"] = (
                "Une ou plusieurs fonctions de la Log Story sont en erreur: "
                f"{', '.join(error_functions)}."
            )
        elif failing_hsm_codes:
            transaction["probable_cause"] = (
                "La trace contient un retour HSM non approuve: "
                f"{', '.join(failing_hsm_codes)}."
            )
        elif fields.get("039") and fields.get("039") != "00":
            transaction["probable_cause"] = (
                "La transaction est consideree en echec car le champ "
                f"FLD 039 vaut {fields.get('039')}."
            )
        else:
            transaction["probable_cause"] = (
                "Transaction marquee en echec par les regles deterministes "
                "du parser."
            )

        transaction["hypotheses"] = [
            "Verifier la premiere fonction ERROR/WARNING dans la Log Story.",
            "Verifier le sens du code FLD 039 dans la documentation PDF.",
        ]
        transaction["recommendations"] = [
            "Ouvrir En savoir plus sur la fonction en erreur si disponible.",
            "Comparer les champs ISO extraits avec les regles documentees.",
        ]


def response_transactions(
    transactions: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    normalized_question = normalize_question_text(question)
    only_failures = any(
        term in normalized_question
        for term in (
            "echec",
            "echecs",
            "echoue",
            "failed",
            "erreur",
            "error",
            "nok",
        )
    )
    important_transactions = [
        transaction
        for transaction in transactions
        if should_enrich_transaction(transaction)
    ]

    if only_failures:
        return important_transactions[:MAX_RESPONSE_TRANSACTIONS]

    if important_transactions:
        remaining = [
            transaction
            for transaction in transactions
            if transaction not in important_transactions
        ]
        return [
            *important_transactions,
            *remaining,
        ][:MAX_RESPONSE_TRANSACTIONS]

    return transactions[:MAX_RESPONSE_TRANSACTIONS]


async def enrich_with_llm(
    question: str,
    transactions: list[dict[str, Any]],
    reference_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    transactions_to_explain = [
        transaction
        for transaction in transactions
        if should_enrich_transaction(transaction)
    ]

    if not transactions_to_explain:
        return {}

    compact_transactions = [
        {
            "transaction_id": transaction["transaction_id"],
            "mti": transaction.get("mti"),
            "fields": transaction.get("fields"),
            "status": transaction.get("status"),
            "error_functions": [
                item
                for item in transaction.get("log_story", [])
                if item.get("status") in {"ERROR", "WARNING"}
            ],
            "observed_facts": transaction.get("observed_facts", []),
            "documentation_findings": transaction.get(
                "documentation_findings",
                [],
            )[:5],
        }
        for transaction in transactions_to_explain[:MAX_LLM_TRANSACTIONS]
    ]
    selected_references = await select_relevant_sections(
        question=question,
        sections=reference_sections,
        limit=6,
        use_embeddings=False,
    ) if reference_sections else []

    messages = [
        {
            "role": "system",
            "content": (
                "You explain deterministic log analysis results. Do not parse "
                "raw logs. Do not invent fields or errors. Return valid JSON: "
                '{"summary": string, "transactions": [{"transaction_id": '
                'string, "probable_cause": string, "hypotheses": string[], '
                '"recommendations": string[]}]}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                "Deterministic parsed transactions:\n"
                f"{json.dumps(compact_transactions, ensure_ascii=False)}\n\n"
                f"Documentation extracts:\n{build_context(selected_references)}"
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
    except (HpsAiConfigurationError, HpsAiRequestError):
        return {}

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def apply_llm_enrichment(
    transactions: list[dict[str, Any]],
    enrichment: dict[str, Any],
) -> str | None:
    enriched_by_id = {
        item.get("transaction_id"): item
        for item in enrichment.get("transactions", [])
        if isinstance(item, dict)
    }

    for transaction in transactions:
        item = enriched_by_id.get(transaction["transaction_id"])

        if not item:
            continue

        transaction["probable_cause"] = item.get("probable_cause", "")
        transaction["hypotheses"] = item.get("hypotheses", [])
        transaction["recommendations"] = item.get("recommendations", [])

    return enrichment.get("summary")


async def answer_log_question(
    question: str,
    conversation_id: str | None,
    referenced_document_ids: list[str],
) -> dict[str, Any]:
    if not conversation_id:
        raise HTTPException(
            status_code=400,
            detail="A conversation_id is required for Log Analysis Agent.",
        )

    log_texts = await load_log_texts(conversation_id)
    log_sections = []

    if not log_texts:
        log_sections = await load_log_sections(conversation_id)

    reference_sections = await load_reference_sections(
        conversation_id=conversation_id,
        referenced_document_ids=referenced_document_ids,
    )
    display_options = display_options_for_question(question)

    if not log_texts and not log_sections:
        return {
            "summary": (
                "Aucune trace extraite n'est disponible dans cette "
                "conversation. Ajoute un fichier .log ou .txt avec "
                "Log Analysis Agent, puis repose ta question."
            ),
            "story": [],
            "issues": [
                {
                    "severity": "warning",
                    "title": "No extracted log found",
                    "detail": (
                        "The Log Analysis Agent needs at least one "
                        "uploaded log file in the current conversation."
                    ),
                }
            ],
            "recommendations": [
                "Switch to Log Analysis Agent.",
                "Attach the trace/log file.",
                "Use # to reference documentation files if needed.",
            ],
            "references": [],
            "transactions": [],
            "statistics": {
                "total_transactions": 0,
                "successful_transactions": 0,
                "failed_transactions": 0,
                "warning_transactions": 0,
            },
            "display_options": display_options,
        }

    transactions = []

    source_texts = log_texts or group_log_texts_by_source(log_sections)

    for source, text in source_texts.items():
        transactions.extend(
            parse_log_transactions(
                text=text,
                source=source,
            )
        )

    await enrich_transactions_with_documentation(
        transactions=transactions,
        reference_sections=reference_sections,
    )

    statistics = build_statistics(transactions)
    apply_deterministic_enrichment(transactions)
    summary = deterministic_summary(
        transactions=transactions,
        statistics=statistics,
    )
    llm_summary = None

    if LOG_ANALYSIS_USE_LLM:
        enrichment = await enrich_with_llm(
            question=question,
            transactions=transactions,
            reference_sections=reference_sections,
        )
        llm_summary = apply_llm_enrichment(
            transactions=transactions,
            enrichment=enrichment,
        )

    if llm_summary:
        summary = llm_summary

    selected_response_transactions, query_constraints = (
        select_transactions_for_question(
            transactions=transactions,
            question=question,
        )
    )

    if query_constraints.get("has_constraints"):
        if selected_response_transactions:
            summary = (
                f"{len(selected_response_transactions)} transaction(s) "
                "match the requested criteria. "
                f"{summary}"
            )
        else:
            summary = (
                "Aucune transaction ne correspond aux criteres demandes. "
                f"{summary}"
            )

    selected_response_transactions = selected_response_transactions or []

    statistics["matched_transactions"] = len(selected_response_transactions)
    statistics["returned_transactions"] = len(
        selected_response_transactions
    )

    return {
        "summary": summary,
        "story": [],
        "issues": [],
        "recommendations": [],
        # "references": [],
        # "evidence": [],
        "transactions": selected_response_transactions,
        "statistics": statistics,
        "display_options": display_options,
    }
