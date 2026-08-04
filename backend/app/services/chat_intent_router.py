import re
import unicodedata
from typing import Literal


Workflow = Literal["DOCUMENTATION_QA", "LOG_COMPLIANCE_ANALYSIS"]


DOCUMENTATION_QA_TERMS = (
    "explique",
    "presente",
    "présente",
    "format",
    "valeur",
    "valeurs",
    "valid values",
    "signification",
    "tableau",
    "table",
    "cite",
    "pages",
    "documentation",
)
LOG_ANALYSIS_TERMS = (
    "analyse cette trace",
    "analyse ce log",
    "detecte les anomalies",
    "détecte les anomalies",
    "verifie cette transaction",
    "vérifie cette transaction",
    "pourquoi cette autorisation",
    "a echoue",
    "a échoué",
    "log story",
    "hsm",
    "trace",
)


def normalize_intent_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())

    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def is_documentation_qa_question(question: str) -> bool:
    """Detecte les questions pures sur documentation, fields, codes ou pages."""
    normalized = normalize_intent_text(question)
    mentions_field = bool(
        re.search(
            r"\b(?:field|fld|champ|de|data element)\s*\(?0*\d{1,3}(?:\.\d+)?\)?",
            normalized,
        )
    )
    mentions_doc_subject = bool(
        re.search(r"\bfonction\b|\bfunction\b|\bcommande\b|\bcommand\b", normalized)
    )
    asks_documentation = any(term in normalized for term in DOCUMENTATION_QA_TERMS)
    asks_log_analysis = any(term in normalized for term in LOG_ANALYSIS_TERMS)

    if mentions_field and asks_documentation and not asks_log_analysis:
        return True

    if mentions_field and re.search(r"\bcodes?\b|\bvaleurs?\b|\bformat\b", normalized):
        return True

    if mentions_doc_subject and asks_documentation and not asks_log_analysis:
        return True

    return False


def classify_chat_workflow(
    *,
    question: str,
    selected_agent: str,
) -> Workflow:
    """Choisit le workflow sans envoyer les questions doc simples en compliance."""
    if selected_agent == "documentation":
        return "DOCUMENTATION_QA"

    if is_documentation_qa_question(question):
        return "DOCUMENTATION_QA"

    return "LOG_COMPLIANCE_ANALYSIS"
