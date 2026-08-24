from datetime import datetime, timezone
import re
from typing import Any


HSM_DOCUMENT_SOURCE_TERMS = (
    "core host commands",
    "pugd",
    "thales",
    "hsm",
)


def clean_fact_text(
    value: str,
) -> str:
    """Nettoie une signification extraite d'un tableau documentaire."""

    cleaned = re.sub(r"\s+", " ", value or "").strip()
    cleaned = re.sub(
        r"\s+(?:or\s+a\s+standard\s+error\s+code\.?).*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    return cleaned.strip(" .;")


def extract_hsm_command_code(
    heading: str,
    text: str,
) -> str:
    """Identifie le code commande HSM documente, si la section le mentionne."""

    combined = f"{heading}\n{text}"

    patterns = (
        r"\b(?P<code>[A-Z]{2})\s+Command\b",
        r"\bCommand\s+Code\b.*?Value\s*['\"](?P<code>[A-Z]{2})['\"]",
        r"\bCommand\s+Code\b\s*['\"]?(?P<code>[A-Z]{2})['\"]?",
    )

    for pattern in patterns:
        match = re.search(pattern, combined, flags=re.IGNORECASE)

        if match:
            return match.group("code").upper()

    return ""


def extract_hsm_response_code(
    heading: str,
    text: str,
) -> str:
    """Identifie le code reponse HSM documente, par exemple ED."""

    combined = f"{heading}\n{text}"

    patterns = (
        r"\b(?P<code>[A-Z]{2})\s+Response\b",
        r"\bResponse\s+Code\b.*?Value\s*['\"](?P<code>[A-Z]{2})['\"]",
        r"\bResponse\s+Code\b\s*['\"]?(?P<code>[A-Z]{2})['\"]?",
    )

    for pattern in patterns:
        match = re.search(pattern, combined, flags=re.IGNORECASE)

        if match:
            return match.group("code").upper()

    return ""


def extract_hsm_error_code_meanings(
    text: str,
) -> list[dict[str, str]]:
    """Extrait les lignes type '01': PIN verification failure."""

    normalized_text = re.sub(r"\s+", " ", text or "")
    code_pattern = re.compile(
        r"['\"]?(?P<code>\d{2})['\"]?\s*[:\-]\s*"
        r"(?P<meaning>.*?)(?="
        r"\s+['\"]?\d{2}['\"]?\s*[:\-]"
        r"|\s+or\s+a\s+standard\s+error\s+code\.?"
        r"|$)",
        flags=re.IGNORECASE,
    )
    rows = []
    seen = set()

    for match in code_pattern.finditer(normalized_text):
        code = match.group("code")
        meaning = clean_fact_text(match.group("meaning"))

        if not meaning:
            continue

        identity = (code, meaning.lower())

        if identity in seen:
            continue

        seen.add(identity)
        rows.append({
            "return_code": code,
            "meaning": meaning,
        })

    return rows


def section_looks_like_hsm_response_table(
    source: str,
    heading: str,
    text: str,
) -> bool:
    """Verifie si une section peut contenir une table de codes retour HSM."""

    source_lower = source.lower()
    combined = f"{heading} {text}".lower()

    if not any(term in source_lower for term in HSM_DOCUMENT_SOURCE_TERMS):
        return False

    return (
        "response message" in combined
        and "error code" in combined
        and "response code" in combined
    )


def extract_hsm_return_code_facts_from_section(
    section: dict[str, Any],
    document: dict[str, Any],
    command_context: str = "",
) -> list[dict[str, Any]]:
    """Transforme une section PDF HSM en facts exploitables par le RAG."""

    source = str(document.get("original_filename") or section.get("source") or "")
    heading = str(section.get("heading") or "")
    text = str(section.get("text") or "")

    if not section_looks_like_hsm_response_table(source, heading, text):
        return []

    response_code = extract_hsm_response_code(heading, text)

    if not response_code:
        return []

    command_code = extract_hsm_command_code(heading, text) or command_context
    rows = extract_hsm_error_code_meanings(text)
    now = datetime.now(timezone.utc)
    facts = []

    for row in rows:
        hsm_result_code = f"{response_code}{row['return_code']}"
        facts.append({
            "fact_type": "hsm_return_code",
            "document_id": str(document.get("_id") or section.get("document_id")),
            "conversation_id": document.get("conversation_id"),
            "agent": document.get("agent"),
            "source": source,
            "extension": document.get("extension"),
            "command": command_code,
            "response_command": response_code,
            "return_code": row["return_code"],
            "hsm_result_code": hsm_result_code,
            "meaning": row["meaning"],
            "page": section.get("page"),
            "sheet": section.get("sheet"),
            "paragraph": section.get("paragraph"),
            "heading": heading,
            "section_index": section.get("section_index"),
            "chunk_index": section.get("chunk_index"),
            "command_context": "same_section"
            if extract_hsm_command_code(heading, text)
            else "previous_section"
            if command_context
            else "",
            "created_at": now,
        })

    return facts


def extract_document_facts(
    sections: list[dict[str, Any]],
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extrait tous les facts structures supportes pour un document."""

    facts = []
    seen = set()
    current_command_context = ""

    ordered_sections = sorted(
        sections,
        key=lambda section: (
            str(section.get("source") or section.get("document_id") or ""),
            int(section.get("section_index") or 0),
            int(section.get("chunk_index") or 0),
            int(section.get("page") or 0),
        ),
    )

    for section in ordered_sections:
        section_command = extract_hsm_command_code(
            heading=str(section.get("heading") or ""),
            text=str(section.get("text") or ""),
        )

        if section_command:
            current_command_context = section_command

        for fact in extract_hsm_return_code_facts_from_section(
            section=section,
            document=document,
            command_context=current_command_context,
        ):
            identity = (
                fact.get("fact_type"),
                fact.get("document_id"),
                fact.get("response_command"),
                fact.get("return_code"),
                fact.get("meaning", "").lower(),
                fact.get("page"),
            )

            if identity in seen:
                continue

            seen.add(identity)
            facts.append(fact)

    return facts
