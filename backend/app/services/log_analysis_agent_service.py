import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from bson import ObjectId
from fastapi import HTTPException

from app.database import (
    document_facts_collection,
    document_sections_collection,
    documents_collection,
    messages_collection,
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
from app.services.file_type_service import (
    with_trace_extension_query,
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
from app.services.screenshot_analysis_service import answer_screenshot_question
from app.guardrails.security.secret_guard import SecretGuardrail


MAX_PDF_REFERENCE_SECTIONS = 4
MAX_HSM_REFERENCE_SECTIONS = 5
MAX_LLM_TRANSACTIONS = 8
MAX_RESPONSE_TRANSACTIONS = 30
MAX_DOCUMENTATION_FINDINGS = 3
BACKEND_ROOT = Path(__file__).resolve().parents[2]
HSM_DOCUMENT_SOURCE_TERMS = (
    "core host commands",
    "pugd",
    "thales",
    "hsm",
)
HSM_DOCUMENT_TEXT_TERMS = (
    "response message",
    "host command",
    "to hsm",
    "from hsm",
    "error code",
)
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
    "0210",
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
    "ordre d'apparition",
    "ordre apparition",
)
SECURITY_AUDIT_TERMS = (
    "api key",
    "apikey",
    "cle api",
    "clé api",
    "token",
    "bearer",
    "secret",
    "client secret",
    "client_secret",
    "password",
    "mot de passe",
    "credential",
    "identifiant sensible",
    "donnee sensible",
    "donnée sensible",
)
REDACTED_SECRET_MARKER_PATTERN = re.compile(
    r"\b(authorization\s*:\s*bearer|api[_-]?key|client[_-]?secret|password|passwd|secret|token)\s*[:=]?\s*\[REDACTED",
    re.IGNORECASE,
)
COMPLIANCE_RULE_EXTRACTION_PROMPT = """
Tu es un extracteur de regles de conformite transactionnelle.

Entrees:
1. Les faits observes dans une trace ISO8583.
2. Des passages recuperes depuis la documentation PDF officielle.

Ta mission consiste uniquement a extraire les regles documentaires
potentiellement applicables a cette transaction.

N'invente aucune regle.
N'utilise pas tes connaissances generales.
Ne declare pas encore d'anomalie.

Retourne uniquement un JSON valide avec la cle "rules".
Si aucun passage ne contient une regle verifiable, retourne {"rules":[]}.
""".strip()
COMPLIANCE_AUDIT_PROMPT = """
Tu es un auditeur de conformite.

Tu recois:
- les faits observes dans la trace;
- les regles documentaires structurees;
- les resultats deterministes de comparaison calcules par le backend.

Tu ne dois pas modifier les resultats de comparaison.
Une anomalie peut etre affichee uniquement lorsque la regle est applicable,
les donnees necessaires sont disponibles, au moins une condition est VIOLATED
et une reference documentaire exacte est disponible.
""".strip()


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


def valid_object_ids(
    document_ids: list[str],
) -> list[ObjectId]:
    return [
        ObjectId(document_id)
        for document_id in document_ids
        if ObjectId.is_valid(document_id)
    ]


def extracted_trace_document_query(
    conversation_id: str,
    object_ids: list[ObjectId] | None = None,
) -> dict[str, Any]:
    query = {
        "conversation_id": conversation_id,
        "status": "extracted",
    }

    if object_ids:
        query["_id"] = {"$in": object_ids}

    return with_trace_extension_query(query)


async def document_ids_containing_logs(
    conversation_id: str,
    document_ids: list[str],
) -> list[str]:
    object_ids = valid_object_ids(document_ids)

    if not object_ids:
        return []

    documents = await documents_collection.find(
        extracted_trace_document_query(
            conversation_id=conversation_id,
            object_ids=object_ids,
        )
    ).to_list(length=50)

    return [
        str(document["_id"])
        for document in documents
    ]


async def latest_message_document_ids(
    conversation_id: str,
) -> list[str]:
    cursor = messages_collection.find(
        {
            "conversation_id": conversation_id,
            "role": "user",
            "attachments.document_id": {"$exists": True},
        }
    ).sort(
        "created_at",
        -1,
    )
    messages = await cursor.to_list(length=20)

    for message in messages:
        document_ids = [
            attachment.get("document_id")
            for attachment in message.get("attachments", [])
            if attachment.get("document_id")
        ]

        if await document_ids_containing_logs(
            conversation_id=conversation_id,
            document_ids=document_ids,
        ):
            return list(dict.fromkeys(document_ids))

    return []


async def resolve_context_document_ids(
    conversation_id: str,
    referenced_document_ids: list[str],
) -> list[str]:
    current_ids = list(dict.fromkeys(referenced_document_ids))

    if await document_ids_containing_logs(
        conversation_id=conversation_id,
        document_ids=current_ids,
    ):
        return current_ids

    previous_ids = await latest_message_document_ids(conversation_id)

    return list(dict.fromkeys([
        *previous_ids,
        *current_ids,
    ]))


async def load_log_documents(
    conversation_id: str,
    referenced_document_ids: list[str],
) -> list[dict[str, Any]]:
    object_ids = valid_object_ids(referenced_document_ids)
    base_query = extracted_trace_document_query(
        conversation_id=conversation_id,
    )

    if object_ids:
        referenced_logs = await documents_collection.find(
            extracted_trace_document_query(
                conversation_id=conversation_id,
                object_ids=object_ids,
            )
        ).sort(
            "created_at",
            1,
        ).to_list(length=50)

        if referenced_logs:
            return referenced_logs

    documents = await documents_collection.find(
        base_query
    ).sort(
        "created_at",
        1,
    ).to_list(length=50)

    return documents


async def load_log_texts(
    conversation_id: str,
    referenced_document_ids: list[str],
) -> dict[str, str]:
    documents = await load_log_documents(
        conversation_id=conversation_id,
        referenced_document_ids=referenced_document_ids,
    )
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


async def load_log_sections(
    conversation_id: str,
    referenced_document_ids: list[str],
) -> list[dict[str, Any]]:
    documents = await load_log_documents(
        conversation_id=conversation_id,
        referenced_document_ids=referenced_document_ids,
    )

    return await load_sections_for_documents(documents)


async def load_reference_sections(
    conversation_id: str,
    referenced_document_ids: list[str],
) -> list[dict[str, Any]]:
    object_ids = valid_object_ids(referenced_document_ids)
    query = {
        "conversation_id": conversation_id,
        "status": "extracted",
        "extension": {"$in": [".pdf", ".xlsx"]},
    }
    conversation_documents = await documents_collection.find(
        query
    ).sort(
        "created_at",
        1,
    ).to_list(length=100)

    if object_ids:
        referenced_documents = await documents_collection.find(
            {
                "_id": {"$in": object_ids},
                "status": "extracted",
                "extension": {"$in": [".pdf", ".xlsx"]},
            }
        ).to_list(length=100)

        if referenced_documents:
            referenced_ids = {
                str(document["_id"])
                for document in referenced_documents
            }
            remaining_documents = [
                document
                for document in conversation_documents
                if str(document["_id"]) not in referenced_ids
            ]

            return await load_sections_for_documents([
                *referenced_documents,
                *remaining_documents,
            ])

    return await load_sections_for_documents(conversation_documents)


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

    ignored_hsm_command_words = {
        "HSM",
        "HOST",
        "CODE",
        "CODES",
        "RETOUR",
        "REPONSE",
        "RESPONSE",
    }
    constraints["hsm_commands"] = [
        command
        for command in {
            match.group(1).upper()
            for match in re.finditer(
                r"\b(?:command|commande|command_)\s*_?\s*([A-Z0-9]{2,4})\b",
                question,
                flags=re.IGNORECASE,
            )
        }
        if command not in ignored_hsm_command_words
    ]

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
    wants_generic_trace_analysis = any(
        term in normalized
        for term in (
            "analyse cette trace",
            "analyse la trace",
            "analyse ce log",
            "analyse le log",
            "analyze this trace",
            "analyze the trace",
        )
    )
    wants_total = (
        any(term in normalized for term in TOTAL_ANALYSIS_TERMS)
        or wants_generic_trace_analysis
    )
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
            "analysis_mode": "total",
            "show_fields": True,
            "show_log_story": True,
            "show_hsm": True,
            "show_documentation_findings": True,
        }

    if wants_hsm:
        return {
            "analysis_mode": "hsm",
            "show_fields": False,
            "show_log_story": False,
            "show_hsm": True,
            "show_documentation_findings": True,
        }

    if only_hsm:
        return {
            "analysis_mode": "hsm",
            "show_fields": False,
            "show_log_story": False,
            "show_hsm": True,
            "show_documentation_findings": True,
        }

    if only_log_story:
        return {
            "analysis_mode": "log",
            "show_fields": True,
            "show_log_story": True,
            "show_hsm": False,
            "show_documentation_findings": True,
        }

    return {
        "analysis_mode": "log",
        "show_fields": True,
        "show_log_story": True,
        "show_hsm": False,
        "show_documentation_findings": True,
    }


def question_requests_security_audit(question: str) -> bool:
    normalized = normalize_question_text(question)
    if not normalized:
        return False

    asks_detection = any(
        term in normalized
        for term in (
            "est-ce qu",
            "est ce qu",
            "y a-t-il",
            "y a t il",
            "existe",
            "detecte",
            "détecte",
            "trouve",
            "indique",
            "affiche",
            "extract",
            "extraire",
            "extrait",
        )
    )

    return asks_detection and any(term in normalized for term in SECURITY_AUDIT_TERMS)


def security_finding_label(code: str, context: str = "") -> str:
    context_normalized = normalize_question_text(context)

    if "authorization" in context_normalized and "bearer" in context_normalized:
        return "Bearer token"
    if (
        "api_key" in context_normalized
        or "api-key" in context_normalized
        or "apikey" in context_normalized
    ):
        return "API key"
    if "client_secret" in context_normalized or "client-secret" in context_normalized:
        return "Client secret"
    if (
        "password" in context_normalized
        or "passwd" in context_normalized
        or "mot de passe" in context_normalized
    ):
        return "Password"
    if code == "PRIVATE_KEY_DETECTED":
        return "Private key"
    if code == "SECRET_PLACEHOLDER_DETECTED":
        return "Placeholder credential"
    if code == "SECRET_DETECTED":
        return "Credential-like secret"
    return code.replace("_", " ").title()


def detect_trace_security_findings(source_texts: dict[str, str]) -> list[dict[str, Any]]:
    findings_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for source, text in source_texts.items():
        value = str(text or "")
        report = SecretGuardrail.inspect_text(value)

        for finding in report.findings:
            context_start = max(finding.start - 40, 0)
            context_end = min(finding.end + 40, len(value))
            context = value[context_start:context_end]
            label = security_finding_label(finding.code, context)
            key = (source, label)
            findings_by_key.setdefault(
                key,
                {
                    "source": source,
                    "type": label,
                    "status": (
                        "placeholder"
                        if finding.code == "SECRET_PLACEHOLDER_DETECTED"
                        else "masked"
                    ),
                    "code": finding.code,
                },
            )

        for match in REDACTED_SECRET_MARKER_PATTERN.finditer(value):
            context_start = max(match.start() - 40, 0)
            context_end = min(match.end() + 40, len(value))
            context = value[context_start:context_end]
            label = security_finding_label("SECRET_DETECTED", context)
            key = (source, label)
            findings_by_key.setdefault(
                key,
                {
                    "source": source,
                    "type": label,
                    "status": "masked",
                    "code": "SECRET_REDACTED_IN_TRACE",
                },
            )

    return list(findings_by_key.values())


def build_trace_security_audit_response(
    *,
    source_texts: dict[str, str],
    display_options: dict[str, bool],
) -> dict[str, Any]:
    findings = detect_trace_security_findings(source_texts)

    if findings:
        detected_types = ", ".join(
            sorted({finding["type"] for finding in findings})
        )
        summary = (
            "Oui. La trace contient des donnees sensibles de type "
            f"{detected_types}. Les valeurs completes ne sont pas affichees "
            "car elles ont ete masquees par les guardrails de securite."
        )
        sections = [
            {
                "title": "Donnees sensibles detectees",
                "content": (
                    "Les elements ci-dessous ont ete identifies dans la trace. "
                    "Les valeurs exactes restent masquees."
                ),
                "items": [
                    {
                        "label": finding["type"],
                        "content": (
                            f"Source: {finding['source']} - "
                            "valeur masquee"
                            if finding["status"] == "masked"
                            else f"Source: {finding['source']} - placeholder"
                        ),
                    }
                    for finding in findings
                ],
            }
        ]
        issues = [
            {
                "severity": "warning",
                "title": "Sensitive trace data detected",
                "detail": (
                    "La trace contient des credentials ou tokens potentiels. "
                    "TRACE ne les affiche pas en clair."
                ),
            }
        ]
        recommendations = [
            "Ne partage jamais une trace contenant des secrets non masques.",
            "Si un token reel a ete expose, considere-le comme compromis et regenere-le.",
        ]
    else:
        summary = (
            "Aucune API key, token Bearer, mot de passe, client secret ou "
            "cle privee n'a ete detecte dans la trace chargee."
        )
        sections = [
            {
                "title": "Controle de securite",
                "content": (
                    "Le controle a cherche des credentials evidents dans le texte "
                    "de trace disponible pour cette conversation."
                ),
                "items": [],
            }
        ]
        issues = []
        recommendations = []

    return {
        "summary": summary,
        "sections": sections,
        "story": [],
        "issues": issues,
        "recommendations": recommendations,
        "references": [],
        "transactions": [],
        "statistics": {
            "total_transactions": 0,
            "successful_transactions": 0,
            "failed_transactions": 0,
            "warning_transactions": len(issues),
        },
        "display_options": {
            **display_options,
            "analysis_mode": "security",
            "show_log_story": True,
            "show_transactions": False,
            "show_downloads": False,
        },
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


def transaction_has_visible_analysis(
    transaction: dict[str, Any],
) -> bool:
    return bool(transaction.get("log_story")) or bool(
        (transaction.get("hsm_analysis") or {}).get("commands")
    )


def transaction_group_key(
    transaction: dict[str, Any],
) -> tuple[str, str]:
    fields = transaction.get("fields") or {}
    rrn = fields.get("037")

    if rrn:
        return (
            str(transaction.get("source") or ""),
            f"rrn:{rrn}",
        )

    return (
        str(transaction.get("source") or ""),
        f"transaction:{transaction.get('transaction_id') or id(transaction)}",
    )


def severity_rank(
    status: str | None,
) -> int:
    return {
        "FAILED": 4,
        "ERROR": 4,
        "WARNING": 3,
        "UNKNOWN": 2,
        "SUCCESS": 1,
    }.get(str(status or "UNKNOWN").upper(), 2)


def strongest_status(
    transactions: list[dict[str, Any]],
) -> str:
    if not transactions:
        return "UNKNOWN"

    return max(
        (str(transaction.get("status") or "UNKNOWN").upper()
         for transaction in transactions),
        key=severity_rank,
    )


def unique_dicts(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique_items = []
    seen = set()

    for item in items:
        if not isinstance(item, dict):
            continue

        identity = json.dumps(
            item,
            sort_keys=True,
            default=str,
        )

        if identity in seen:
            continue

        seen.add(identity)
        unique_items.append(item)

    return unique_items


def merge_hsm_analysis_group(
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    commands = []
    result_codes = []
    threads = []
    documentation_findings = []

    for transaction in transactions:
        hsm_analysis = transaction.get("hsm_analysis") or {}

        if hsm_analysis.get("thread"):
            threads.append(str(hsm_analysis["thread"]))

        result_codes.extend(
            str(code)
            for code in hsm_analysis.get("result_codes", [])
            if code
        )
        commands.extend(
            command
            for command in hsm_analysis.get("commands", [])
            if isinstance(command, dict)
        )
        documentation_findings.extend(
            finding
            for finding in hsm_analysis.get("documentation_findings", [])
            if isinstance(finding, dict)
        )

    commands = unique_dicts(commands)

    for order, command in enumerate(commands, start=1):
        command["order"] = order

    return {
        "thread": ", ".join(dict.fromkeys(threads)),
        "commands": commands,
        "result_codes": list(dict.fromkeys(result_codes)),
        "documentation_findings": unique_dicts(documentation_findings),
    }


def merge_log_story_group(
    transactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    story = []

    for transaction in transactions:
        story.extend(
            item
            for item in transaction.get("log_story", [])
            if isinstance(item, dict)
        )

    for order, item in enumerate(story, start=1):
        item["order"] = order

    return story


def merge_fields_group(
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    merged_fields: dict[str, Any] = {}

    for transaction in transactions:
        for field, value in (transaction.get("fields") or {}).items():
            if value in (None, ""):
                continue

            if field not in merged_fields or not merged_fields[field]:
                merged_fields[field] = value

    response_candidates = [
        transaction
        for transaction in transactions
        if (transaction.get("fields") or {}).get("039")
    ]

    if response_candidates:
        final_response = max(
            response_candidates,
            key=lambda transaction: (
                1
                if transaction.get("mti") in {"0110", "0210"}
                else 0,
                transaction.get("start_line") or 0,
                transaction.get("log_index") or 0,
            ),
        )
        merged_fields["039"] = (
            final_response.get("fields") or {}
        ).get("039")

    return merged_fields


def merge_business_transaction_group(
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered_transactions = sorted(
        transactions,
        key=lambda transaction: (
            transaction.get("start_line") or 0,
            transaction.get("log_index") or 0,
        ),
    )
    primary = dict(ordered_transactions[0])
    fields = merge_fields_group(ordered_transactions)
    log_story = merge_log_story_group(ordered_transactions)
    hsm_analysis = merge_hsm_analysis_group(ordered_transactions)
    documentation_findings = unique_dicts([
        finding
        for transaction in ordered_transactions
        for finding in transaction.get("documentation_findings", [])
        if isinstance(finding, dict)
    ])[:MAX_DOCUMENTATION_FINDINGS]
    sources = unique_dicts([
        source
        for transaction in ordered_transactions
        for source in transaction.get("sources", [])
        if isinstance(source, dict)
    ])
    evidence = unique_dicts([
        evidence_item
        for transaction in ordered_transactions
        for evidence_item in transaction.get("evidence", [])
        if isinstance(evidence_item, dict)
    ])
    related_mtis = [
        str(transaction.get("mti"))
        for transaction in ordered_transactions
        if transaction.get("mti")
    ]

    primary.update({
        "transaction_id": (
            f"group_{fields.get('037')}"
            if fields.get("037")
            else primary.get("transaction_id")
        ),
        "grouped_transaction_ids": [
            transaction.get("transaction_id")
            for transaction in ordered_transactions
            if transaction.get("transaction_id")
        ],
        "grouped_log_indices": [
            transaction.get("log_index")
            for transaction in ordered_transactions
            if transaction.get("log_index") is not None
        ],
        "related_mtis": list(dict.fromkeys(related_mtis)),
        "fields": fields,
        "status": strongest_status(ordered_transactions),
        "log_story": log_story,
        "hsm_analysis": hsm_analysis,
        "documentation_findings": documentation_findings,
        "sources": sources,
        "evidence": evidence,
        "start_line": min(
            transaction.get("start_line") or 0
            for transaction in ordered_transactions
        ),
        "end_line": max(
            transaction.get("end_line") or 0
            for transaction in ordered_transactions
        ),
    })
    primary["observed_facts"] = observed_facts_for_transaction(primary)
    primary["display_name"] = (
        f"FLD 037 {fields.get('037')}"
        if fields.get("037")
        else primary.get("display_name")
    )

    return primary


def is_merged_response_block(
    transaction: dict[str, Any],
) -> bool:
    return bool(transaction.get("merged_into_transaction_id"))


def visible_response_transactions(
    transactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    visible_transactions = [
        transaction
        for transaction in transactions
        if (
            transaction_has_visible_analysis(transaction)
            and not is_merged_response_block(transaction)
        )
    ]
    grouped_transactions: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for transaction in visible_transactions:
        grouped_transactions.setdefault(
            transaction_group_key(transaction),
            [],
        ).append(transaction)

    return [
        merge_business_transaction_group(group)
        for group in grouped_transactions.values()
    ]


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

    return (
        visible_response_transactions(matched_transactions)[
            :MAX_RESPONSE_TRANSACTIONS
        ],
        constraints,
    )


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

    log_story = transaction.get("log_story", [])

    for order, item in enumerate(log_story, start=1):
        item["order"] = order
        item["documented_in_excel"] = (
            normalize_function_name(item.get("function_name", ""))
            in documented_names
        )

    transaction["log_story"] = log_story


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


def normalize_field_number(
    value: Any,
) -> str:
    raw_value = str(value or "").strip()

    if not raw_value:
        return ""

    if "." in raw_value:
        first_part, second_part = raw_value.split(".", 1)
        return f"{first_part.zfill(3)}.{second_part}"

    return raw_value.zfill(3)


def section_field_numbers(
    section: dict[str, Any],
) -> set[str]:
    heading = str(section.get("heading") or "")

    for source_text in (heading, str(section.get("text") or "")):
        numbers = set()

        for match in re.finditer(
            r"\b(?:Field|FLD)\s*\(?0*(\d{1,3}(?:\.\d+)?)\)?\b",
            source_text,
            flags=re.IGNORECASE,
        ):
            numbers.add(normalize_field_number(match.group(1)))

        if numbers:
            return numbers

    return set()


def extract_fixed_length_rule(
    section: dict[str, Any],
) -> dict[str, Any] | None:
    text = re.sub(
        r"\s+",
        " ",
        " ".join(
            str(value or "")
            for value in (
                section.get("heading"),
                section.get("text"),
            )
        ),
    ).strip()

    if not re.search(r"\bfixed\s+length\b", text, flags=re.IGNORECASE):
        return None

    match = re.search(
        r"\bfixed\s+length\s+(\d{1,4})\s*([A-Z]{1,8})?",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    return {
        "length": int(match.group(1)),
        "format": (match.group(2) or "").upper(),
        "rule_text": compact_text(match.group(0), limit=120),
    }


def build_pdf_field_length_rules(
    pdf_sections: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rules: dict[str, dict[str, Any]] = {}

    for section in pdf_sections:
        length_rule = extract_fixed_length_rule(section)

        if not length_rule:
            continue

        for field in section_field_numbers(section):
            existing = rules.get(field)

            if existing and existing.get("page") <= (section.get("page") or 0):
                continue

            reference = reference_from_section(section)
            format_suffix = (
                f" {length_rule['format']}"
                if length_rule["format"]
                else ""
            )
            format_description = (
                f" et un format {length_rule['format']}"
                if length_rule["format"]
                else ""
            )
            rules[field] = {
                "field": field,
                "operator": "length_equals",
                "expected": length_rule["length"],
                "format": length_rule["format"],
                "expected_condition": (
                    f"Longueur fixe {length_rule['length']}"
                    f"{format_suffix}"
                ),
                "description": (
                    f"Le PDF indique que le Field {field} a une longueur fixe "
                    f"de {length_rule['length']}"
                    f"{format_description}."
                ),
                "rule_text": length_rule["rule_text"],
                "section": section,
                "source_reference": reference,
                "page": section.get("page") or 0,
            }

    return rules


def clean_field_value_for_length_check(
    value: Any,
) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text or text.upper() == "N/A" or "*" in text:
        return None

    return re.sub(r"\s+", "", text)


def build_pdf_length_documentation_findings(
    transaction: dict[str, Any],
    field_length_rules: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    findings = []
    fields = transaction.get("fields") or {}

    for field, raw_value in fields.items():
        normalized_field = normalize_field_number(field)
        rule = field_length_rules.get(normalized_field)

        if not rule:
            continue

        observed_value = clean_field_value_for_length_check(raw_value)

        if observed_value is None:
            continue

        observed_length = len(observed_value)
        expected_length = rule["expected"]

        if observed_length == expected_length:
            continue

        reference = rule.get("source_reference") or {}
        section = rule.get("section") or {}

        findings.append({
            "source_type": "pdf",
            "type": "length_non_conformity",
            "anomaly": (
                f"Longueur non conforme du FLD {normalized_field}."
            ),
            "field": normalized_field,
            "observed_value": (
                f"{raw_value} ({observed_length} caractere(s))"
            ),
            "expected_condition": (
                f"{rule['expected_condition']} "
                f"({expected_length} caractere(s) attendu(s))"
            ),
            "context": f"MTI {transaction.get('mti') or 'UNKNOWN'}",
            "expected_rule": rule["description"],
            "explanation": (
                "La valeur observee ne respecte pas la longueur fixe "
                "documentee pour ce champ."
            ),
            "rule": {
                "rule_id": f"field_{normalized_field}_length",
                "description": rule["description"],
                "conditions": [
                    {
                        "field": normalized_field,
                        "operator": "length_equals",
                        "expected": expected_length,
                    }
                ],
                "reference": {
                    "document": reference.get("source", ""),
                    "page": reference.get("page"),
                    "section": section.get("heading") or reference.get("section", ""),
                    "table": "",
                },
            },
            "comparison": {
                "rule_id": f"field_{normalized_field}_length",
                "status": "ANOMALY",
                "condition_results": [
                    {
                        "field": normalized_field,
                        "operator": "length_equals",
                        "observed": observed_value,
                        "expected": expected_length,
                        "status": "VIOLATED",
                    }
                ],
            },
            "conclusion": "Anomalie demontree par la documentation",
            **reference,
            "heading": section.get("heading"),
            "evidence": rule.get("rule_text") or rule["description"],
        })

        if len(findings) >= MAX_DOCUMENTATION_FINDINGS:
            break

    return findings


def observed_pdf_anomalies(
    transaction: dict[str, Any],
) -> list[dict[str, str]]:
    fields = transaction.get("fields", {})
    mti = transaction.get("mti")
    anomalies = []

    # A documentation anomaly must be demonstrated by an explicit PDF rule.
    # Missing request fields or a non-00 response code are trace facts only;
    # they are not documentary anomalies unless a matching PDF rule proves it.
    if mti in FIELD_039_REQUIRED_RESPONSE_MTIS and not fields.get("039"):
        anomalies.append({
            "type": "missing_response_field_039",
            "field": "039",
            "anomaly": f"FLD 039 is missing from response MTI {mti}.",
            "observed_value": "missing",
            "expected_condition": f"FLD 039 present in response MTI {mti}",
            "context": f"MTI {mti} response message",
            "query_terms": (
                "Field 39 is required in all 0110 0130 0310 0312 "
                "0410 0430 responses reject code 0294 Field missing"
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
        mti_match = re.search(r"\b(\d{4})\b", anomaly.get("context", ""))
        mti = mti_match.group(1) if mti_match else ""
        has_field_39 = (
            "field 39" in text
            or "field 039" in text
            or "response code" in text
        )
        has_required_rule = (
            "required in all" in text
            or "must be present" in text
            or "mandatory" in text
        )
        has_applicable_context = (
            "all responses" in text
            or "response message" in text
            or "responses" in text
            or (mti and mti in text)
        )
        return (
            has_field_39
            and has_required_rule
            and has_applicable_context
        )

    return False


def build_rule_from_anomaly(
    anomaly: dict[str, str],
    section: dict[str, Any],
    excerpt: str,
) -> dict[str, Any]:
    reference = reference_from_section(section)

    return {
        "rule_id": anomaly.get("type") or "",
        "description": excerpt,
        "applicability": {
            "mti": [
                value
                for value in re.findall(
                    r"\b\d{4}\b",
                    anomaly.get("context", ""),
                )
            ],
            "processing_codes": [],
            "response_codes": [],
            "required_context": [
                anomaly.get("context", ""),
            ],
        },
        "conditions": [
            {
                "field": anomaly.get("field", ""),
                "operator": "present",
                "expected": anomaly.get("expected_condition", "present"),
            }
        ],
        "exceptions": [],
        "reference": {
            "document": reference.get("source", ""),
            "page": reference.get("page"),
            "section": section.get("heading") or reference.get("section", ""),
            "table": "",
        },
        "source_reference": reference,
    }


def transaction_field_value(
    transaction: dict[str, Any],
    field: str,
) -> Any:
    if not field:
        return None

    normalized_field = str(field).zfill(3)
    return (transaction.get("fields") or {}).get(normalized_field)


def condition_comparison_result(
    transaction: dict[str, Any],
    condition: dict[str, Any],
) -> dict[str, Any]:
    field = str(condition.get("field") or "").zfill(3)
    operator = condition.get("operator")
    observed = transaction_field_value(transaction, field)
    expected = condition.get("expected")

    if operator == "present":
        if observed is None or observed == "":
            status = "VIOLATED"
        else:
            status = "COMPLIANT"
    elif operator == "absent":
        status = "COMPLIANT" if not observed else "VIOLATED"
    elif operator == "equals":
        status = "COMPLIANT" if str(observed) == str(expected) else "VIOLATED"
    elif operator == "not_equals":
        status = "COMPLIANT" if str(observed) != str(expected) else "VIOLATED"
    elif operator == "one_of":
        expected_values = expected if isinstance(expected, list) else []
        status = (
            "COMPLIANT"
            if observed in expected_values
            else "VIOLATED"
        )
    elif operator == "numeric_zero":
        status = (
            "COMPLIANT"
            if str(observed or "").isdigit() and int(str(observed)) == 0
            else "VIOLATED"
        )
    elif operator == "length_equals":
        observed_value = clean_field_value_for_length_check(observed)

        if observed_value is None:
            status = "NOT_VERIFIABLE"
        else:
            status = (
                "COMPLIANT"
                if len(observed_value) == int(expected)
                else "VIOLATED"
            )
    else:
        status = "NOT_VERIFIABLE"

    return {
        "field": field,
        "operator": operator,
        "observed": observed if observed not in {None, ""} else "missing",
        "expected": expected,
        "status": status,
    }


def rule_applicability_result(
    transaction: dict[str, Any],
    rule: dict[str, Any],
) -> str:
    applicability = rule.get("applicability") or {}
    applicable_mtis = [
        str(value)
        for value in applicability.get("mti", [])
        if value
    ]

    if applicable_mtis and transaction.get("mti") not in applicable_mtis:
        return "NOT_APPLICABLE"

    return "APPLICABLE"


def compare_transaction_to_rule(
    transaction: dict[str, Any],
    rule: dict[str, Any],
) -> dict[str, Any]:
    applicability = rule_applicability_result(transaction, rule)

    if applicability != "APPLICABLE":
        return {
            "rule_id": rule.get("rule_id", ""),
            "status": applicability,
            "condition_results": [],
        }

    condition_results = [
        condition_comparison_result(transaction, condition)
        for condition in rule.get("conditions", [])
    ]

    if not condition_results:
        status = "NOT_VERIFIABLE"
    elif any(item["status"] == "VIOLATED" for item in condition_results):
        status = "ANOMALY"
    elif any(
        item["status"] == "NOT_VERIFIABLE"
        for item in condition_results
    ):
        status = "NOT_VERIFIABLE"
    else:
        status = "COMPLIANT"

    return {
        "rule_id": rule.get("rule_id", ""),
        "status": status,
        "condition_results": condition_results,
    }


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
            rule = build_rule_from_anomaly(
                anomaly=anomaly,
                section=section,
                excerpt=excerpt,
            )
            comparison = compare_transaction_to_rule(
                transaction=transaction,
                rule=rule,
            )

            if comparison.get("status") != "ANOMALY":
                continue

            reference = rule.get("source_reference") or {}
            violated_conditions = [
                item
                for item in comparison.get("condition_results", [])
                if item.get("status") == "VIOLATED"
            ]
            primary_violation = violated_conditions[0] if violated_conditions else {}

            findings.append({
                "source_type": "pdf",
                "type": "anomaly_justification",
                "anomaly": anomaly["anomaly"],
                "field": anomaly.get("field", ""),
                "observed_value": primary_violation.get(
                    "observed",
                    anomaly["observed_value"],
                ),
                "expected_condition": anomaly.get("expected_condition", ""),
                "context": anomaly.get("context", ""),
                "expected_rule": excerpt,
                "explanation": (
                    "La regle documentaire s'applique au contexte de la "
                    "transaction et la valeur observee la viole clairement."
                ),
                "rule": rule,
                "comparison": comparison,
                "conclusion": "Anomalie demontree par la documentation",
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


def is_hsm_reference_section(
    section: dict[str, Any],
) -> bool:
    source = str(section.get("source") or "").lower()
    heading = str(section.get("heading") or "").lower()
    text = str(section.get("text") or "").lower()

    if any(term in source for term in HSM_DOCUMENT_SOURCE_TERMS):
        return True

    if "base i technical specifications" in source:
        return False

    combined = f"{heading} {text}"

    return (
        "hsm" in combined
        and any(term in combined for term in HSM_DOCUMENT_TEXT_TERMS)
    )


def hsm_document_sections(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        section
        for section in sections
        if is_hsm_reference_section(section)
    ]


def clean_hsm_meaning(
    value: str,
) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    cleaned = re.sub(
        r"\s+(?:'?\d{2}'?\s*[:\-].*)$",
        "",
        cleaned,
    ).strip()

    return cleaned.strip(" .;")


def extract_hsm_return_code_meaning(
    section: dict[str, Any],
    command: dict[str, Any],
) -> str:
    text = str(section.get("text") or "")
    response_command = str(command.get("response_command") or "").upper()
    result_code = str(command.get("hsm_result_code") or "").upper()
    return_code = str(command.get("return_code") or result_code[2:]).upper()

    if not return_code:
        return ""

    normalized_text = re.sub(r"\s+", " ", text)

    if response_command:
        response_patterns = (
            rf"\bvalue\s*['\"]?{re.escape(response_command)}['\"]?",
            rf"\bresponse\s+code\b.*?['\"]?{re.escape(response_command)}['\"]?",
            rf"\b{re.escape(response_command)}\b",
        )
        if not any(
            re.search(pattern, normalized_text, flags=re.IGNORECASE)
            for pattern in response_patterns
        ):
            return ""

    code_pattern = re.compile(
        rf"['\"]?{re.escape(return_code)}['\"]?\s*[:\-]\s*"
        r"(?P<meaning>.*?)(?=\s+['\"]?\d{2}['\"]?\s*[:\-]|\s+or\s+a\s+standard\s+error\s+code\.|$)",
        flags=re.IGNORECASE,
    )
    match = code_pattern.search(normalized_text)

    if match:
        return clean_hsm_meaning(match.group("meaning"))

    line_pattern = re.compile(
        rf"^\s*['\"]?{re.escape(return_code)}['\"]?\s*[:\-]\s*(?P<meaning>.+)$",
        flags=re.IGNORECASE,
    )

    for line in text.splitlines():
        line_match = line_pattern.search(line)
        if line_match:
            return clean_hsm_meaning(line_match.group("meaning"))

    return ""


def pdf_section_supports_hsm_command(
    section: dict[str, Any],
    command: dict[str, Any],
) -> bool:
    if not is_hsm_reference_section(section):
        return False

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


def hsm_section_mentions_command_code(
    section: dict[str, Any],
    command_code: str,
) -> bool:
    if not command_code:
        return True

    combined = " ".join(
        str(value or "")
        for value in (
            section.get("heading"),
            section.get("text"),
        )
    )

    patterns = (
        rf"\b{re.escape(command_code)}\s+command\b",
        rf"\bcommand\s+{re.escape(command_code)}\b",
        rf"\bcommand_{re.escape(command_code)}\b",
        rf"\bvalue\s*['\"]?{re.escape(command_code)}['\"]?",
    )

    return any(
        re.search(pattern, combined, flags=re.IGNORECASE)
        for pattern in patterns
    )


def ordered_hsm_sections(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        sections,
        key=lambda section: (
            str(section.get("document_id") or section.get("source") or ""),
            int(section.get("section_index") or 0),
            int(section.get("chunk_index") or 0),
            int(section.get("page") or 0),
        ),
    )


def hsm_sections_with_command_context(
    sections: list[dict[str, Any]],
    command_code: str,
    context_window: int = 6,
) -> list[dict[str, Any]]:
    if not command_code:
        return ordered_hsm_sections(sections)

    command_code = command_code.upper()
    candidates = []
    last_command_position_by_document: dict[str, int] = {}

    for position, section in enumerate(ordered_hsm_sections(sections)):
        document_key = str(
            section.get("document_id")
            or section.get("source")
            or "document"
        )

        if hsm_section_mentions_command_code(section, command_code):
            last_command_position_by_document[document_key] = position

        last_position = last_command_position_by_document.get(document_key)

        if last_position is None:
            continue

        if position - last_position <= context_window:
            candidates.append(section)

    return candidates


def documented_hsm_value(
    value: str | None,
) -> str:
    return value or "Non trouve dans la documentation fournie"


def reference_from_hsm_fact(
    fact: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source": fact.get("source") or "Document",
        "original_source": fact.get("source") or "Document",
        "document_id": str(fact.get("document_id") or ""),
        "page": fact.get("page"),
        "pdf_page": fact.get("page"),
        "printed_page": fact.get("printed_page"),
        "section": fact.get("heading"),
        "heading": fact.get("heading"),
        "sheet": fact.get("sheet"),
        "paragraph": fact.get("paragraph"),
    }


async def find_structured_hsm_return_code_fact(
    command: dict[str, Any],
    hsm_pdf_sections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    document_ids = list(dict.fromkeys(
        str(section.get("document_id"))
        for section in hsm_pdf_sections
        if section.get("document_id")
    ))
    response_command = str(command.get("response_command") or "").upper()
    result_code = str(command.get("hsm_result_code") or "").upper()
    return_code = str(command.get("return_code") or result_code[2:]).upper()

    if not document_ids or not response_command or not return_code:
        return None

    query = {
        "fact_type": "hsm_return_code",
        "document_id": {"$in": document_ids},
        "response_command": response_command,
        "return_code": return_code,
    }
    command_code = str(command.get("command") or "").upper()

    if command_code:
        return await document_facts_collection.find_one({
            **query,
            "command": command_code,
        })

    return await document_facts_collection.find_one(query)


def hsm_finding_from_structured_fact(
    command: dict[str, Any],
    transaction_thread: str | None,
    fact: dict[str, Any],
) -> dict[str, Any]:
    code_meaning = str(fact.get("meaning") or "").strip()
    return_code = str(command.get("return_code") or fact.get("return_code") or "")
    documented_code_line = (
        f"'{return_code}': {code_meaning}"
        if return_code and code_meaning
        else code_meaning
    )
    explanation = (
        f"{command.get('hsm_result_code')} correspond a la reponse "
        f"{command.get('response_command')} avec le code retour "
        f"{command.get('return_code')}. Signification documentee: "
        f"{documented_code_line}."
    )

    return {
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
        "thread": command.get("thread") or transaction_thread,
        "explanation": explanation,
        "return_code_meaning": code_meaning,
        "documented_return_code_line": documented_code_line,
        **reference_from_hsm_fact(fact),
        "evidence": explanation,
    }


def hsm_finding_from_section_meaning(
    command: dict[str, Any],
    transaction_thread: str | None,
    section: dict[str, Any],
    code_meaning: str,
) -> dict[str, Any]:
    return_code = str(command.get("return_code") or "")
    documented_code_line = (
        f"'{return_code}': {code_meaning}"
        if return_code and code_meaning
        else code_meaning
    )
    explanation = (
        f"{command.get('hsm_result_code')} correspond a la reponse "
        f"{command.get('response_command')} avec le code retour "
        f"{command.get('return_code')}. Signification documentee: "
        f"{documented_code_line}."
    )

    return {
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
        "thread": command.get("thread") or transaction_thread,
        "explanation": explanation,
        "return_code_meaning": code_meaning,
        "documented_return_code_line": documented_code_line,
        **reference_from_section(section),
        "heading": section.get("heading"),
        "evidence": explanation,
    }


def find_hsm_return_code_meaning_section(
    command: dict[str, Any],
    hsm_pdf_sections: list[dict[str, Any]],
) -> tuple[dict[str, Any], str] | tuple[None, str]:
    command_code = str(command.get("command") or "").upper()
    candidate_sections = hsm_sections_with_command_context(
        sections=hsm_pdf_sections,
        command_code=command_code,
    )

    for section in candidate_sections:
        code_meaning = extract_hsm_return_code_meaning(
            section=section,
            command=command,
        )

        if code_meaning:
            return section, code_meaning

    return None, ""


async def build_hsm_documentation_findings(
    transaction: dict[str, Any],
    pdf_sections: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    hsm_analysis = transaction.get("hsm_analysis") or {}
    findings = []
    hsm_pdf_sections = hsm_document_sections(pdf_sections)

    for command in hsm_analysis.get("commands", []):
        if not command.get("request_message") and not command.get("hsm_result_code"):
            continue

        structured_fact = await find_structured_hsm_return_code_fact(
            command=command,
            hsm_pdf_sections=hsm_pdf_sections,
        )

        if structured_fact:
            finding = hsm_finding_from_structured_fact(
                command=command,
                transaction_thread=hsm_analysis.get("thread"),
                fact=structured_fact,
            )
            findings.append(finding)
            command_findings = [finding]
            primary_heading = structured_fact.get("heading")
            primary_explanation = finding["explanation"]
            primary_code_meaning = finding["return_code_meaning"]

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
                primary_code_meaning
            )
            command["documented_return_code_line"] = documented_hsm_value(
                finding.get("documented_return_code_line")
            )
            command["functional_result"] = (
                "SUCCESS"
                if command.get("status") == "SUCCESS"
                else "FAILED"
                if command.get("status") == "FAILED"
                else command.get("status") or "UNKNOWN"
            )
            command["technical_interpretation"] = documented_hsm_value(
                primary_explanation
            )
            command["documentation_findings"] = command_findings
            continue

        exact_section, exact_code_meaning = find_hsm_return_code_meaning_section(
            command=command,
            hsm_pdf_sections=hsm_pdf_sections,
        )

        if exact_section and exact_code_meaning:
            finding = hsm_finding_from_section_meaning(
                command=command,
                transaction_thread=hsm_analysis.get("thread"),
                section=exact_section,
                code_meaning=exact_code_meaning,
            )
            findings.append(finding)
            command_findings = [finding]
            primary_heading = exact_section.get("heading")
            primary_explanation = finding["explanation"]

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
                exact_code_meaning
            )
            command["documented_return_code_line"] = documented_hsm_value(
                finding.get("documented_return_code_line")
            )
            command["functional_result"] = (
                "SUCCESS"
                if command.get("status") == "SUCCESS"
                else "FAILED"
                if command.get("status") == "FAILED"
                else command.get("status") or "UNKNOWN"
            )
            command["technical_interpretation"] = documented_hsm_value(
                primary_explanation
            )
            command["documentation_findings"] = command_findings
            continue

        command_query = " ".join([
            query,
            hsm_command_query(command),
        ])
        selected_pdf_sections = (
            await select_relevant_sections(
                question=command_query,
                sections=hsm_pdf_sections,
                limit=MAX_HSM_REFERENCE_SECTIONS,
                use_embeddings=False,
            )
            if hsm_pdf_sections
            else []
        )
        command_findings = []

        for section in selected_pdf_sections:
            if not pdf_section_supports_hsm_command(section, command):
                continue

            code_meaning = extract_hsm_return_code_meaning(
                section=section,
                command=command,
            )

            if not code_meaning:
                continue

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
                "explanation": (
                    f"{command.get('hsm_result_code')} correspond a la reponse "
                    f"{command.get('response_command')} avec le code retour "
                    f"{command.get('return_code')}. Signification documentee: "
                    f"'{command.get('return_code')}': {code_meaning}."
                ),
                "return_code_meaning": code_meaning,
                "documented_return_code_line": (
                    f"'{command.get('return_code')}': {code_meaning}"
                    if command.get("return_code")
                    else code_meaning
                ),
                **reference_from_section(section),
                "heading": section.get("heading"),
                "evidence": (
                    f"{command.get('hsm_result_code')} correspond a la reponse "
                    f"{command.get('response_command')} avec le code retour "
                    f"{command.get('return_code')}. Signification documentee: "
                    f"'{command.get('return_code')}': {code_meaning}."
                ),
            }
            command_findings.append(finding)
            findings.append(finding)

            if code_meaning or len(command_findings) >= 2:
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
        primary_code_meaning = (
            primary_finding.get("return_code_meaning")
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
            primary_code_meaning
            or primary_explanation
        )
        command["documented_return_code_line"] = documented_hsm_value(
            primary_finding.get("documented_return_code_line")
            if primary_finding
            else ""
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
    field_length_rules = build_pdf_field_length_rules(pdf_sections)

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
            build_pdf_length_documentation_findings(
                transaction=transaction,
                field_length_rules=field_length_rules,
            )
            + build_pdf_documentation_findings(
                transaction=transaction,
                selected_pdf_sections=selected_pdf_sections,
                query=query,
            )
        )[:MAX_DOCUMENTATION_FINDINGS]
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


def question_requests_hsm(
    question: str,
) -> bool:
    normalized_question = normalize_question_text(question)

    return any(
        term in normalized_question
        for term in HSM_ANALYSIS_TERMS
    )


def hsm_command_count(
    transactions: list[dict[str, Any]],
) -> int:
    return sum(
        len((transaction.get("hsm_analysis") or {}).get("commands", []))
        for transaction in transactions
    )


def no_hsm_analysis_message(
    summary: str,
) -> str:
    return (
        "Aucune interaction HSM n'a ete detectee dans la trace active. "
        "Le parser a cherche les marqueurs TO HSM, FROM HSM, "
        "HsmResultCode, command_XX(), WriteBalHsm, ReadBalHsm et "
        "HsmQuery, mais aucun bloc HSM exploitable n'a ete trouve. "
        f"{summary}"
    )


def no_hsm_issue() -> dict[str, str]:
    return {
        "severity": "warning",
        "title": "No HSM interaction found",
        "detail": (
            "La trace active ne contient pas de commande HSM detectable "
            "avec les marqueurs supportes."
        ),
    }


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
    wants_hsm_focus = (
        any(term in normalized_question for term in HSM_ANALYSIS_TERMS)
        and not any(term in normalized_question for term in TOTAL_ANALYSIS_TERMS)
    )
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
    visible_transactions = visible_response_transactions(transactions)
    hsm_transactions = [
        transaction
        for transaction in visible_transactions
        if (transaction.get("hsm_analysis") or {}).get("commands")
    ]

    if wants_hsm_focus:
        return hsm_transactions[:MAX_RESPONSE_TRANSACTIONS]

    important_transactions = [
        transaction
        for transaction in visible_transactions
        if should_enrich_transaction(transaction)
    ]

    if only_failures:
        return important_transactions[:MAX_RESPONSE_TRANSACTIONS]

    if important_transactions:
        remaining = [
            transaction
            for transaction in visible_transactions
            if transaction not in important_transactions
        ]
        return [
            *important_transactions,
            *remaining,
        ][:MAX_RESPONSE_TRANSACTIONS]

    return visible_transactions[:MAX_RESPONSE_TRANSACTIONS]


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

    effective_document_ids = await resolve_context_document_ids(
        conversation_id=conversation_id,
        referenced_document_ids=referenced_document_ids,
    )

    screenshot_response = await answer_screenshot_question(
        question=question,
        conversation_id=conversation_id,
        referenced_document_ids=effective_document_ids,
        agent="log",
    )

    if screenshot_response is not None:
        return screenshot_response

    log_texts = await load_log_texts(
        conversation_id=conversation_id,
        referenced_document_ids=effective_document_ids,
    )
    log_sections = []

    if not log_texts:
        log_sections = await load_log_sections(
            conversation_id=conversation_id,
            referenced_document_ids=effective_document_ids,
        )

    reference_sections = await load_reference_sections(
        conversation_id=conversation_id,
        referenced_document_ids=effective_document_ids,
    )
    display_options = display_options_for_question(question)

    if not log_texts and not log_sections:
        return {
            "summary": (
                "Aucune trace extraite n'est disponible dans cette "
                "conversation. Ajoute un fichier trace (.txt, .log ou .trcNNN) avec "
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

    if question_requests_security_audit(question):
        return build_trace_security_audit_response(
            source_texts=source_texts,
            display_options=display_options,
        )

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

    statistics = build_statistics(
        visible_response_transactions(transactions)
    )
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
    issues = []
    recommendations = []

    if question_requests_hsm(question) and hsm_command_count(transactions) == 0:
        summary = no_hsm_analysis_message(summary)
        issues.append(no_hsm_issue())
        recommendations.extend([
            "Verifier que le fichier attache est bien la trace qui contient les lignes TO HSM / FROM HSM.",
            "Si les commandes HSM utilisent un autre format de ligne, fournir un extrait pour ajouter ce marqueur au parser.",
        ])
    elif (
        question_requests_hsm(question)
        and not selected_response_transactions
    ):
        summary = no_hsm_analysis_message(summary)
        issues.append(no_hsm_issue())
        recommendations.extend([
            "Verifier que le fichier attache est bien la trace qui contient les lignes TO HSM / FROM HSM.",
            "Si tu veux reutiliser la derniere trace HSM, pose la question sans attacher une autre trace.",
        ])

    statistics["matched_transactions"] = len(selected_response_transactions)
    statistics["returned_transactions"] = len(
        selected_response_transactions
    )

    return {
        "summary": summary,
        "story": [],
        "issues": issues,
        "recommendations": recommendations,
        # "references": [],
        # "evidence": [],
        "transactions": selected_response_transactions,
        "statistics": statistics,
        "display_options": display_options,
    }
