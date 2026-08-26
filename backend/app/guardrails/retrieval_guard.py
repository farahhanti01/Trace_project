import logging
import re
from typing import Any

from app.guardrails.models import GuardrailResult, GuardrailStatus
from app.models.document_content import EvidenceBundle


logger = logging.getLogger(__name__)


DOCUMENTARY_INTENTS = {
    "DEFINITION",
    "DIAGNOSTIC_SUPPORT",
    "FIELD_LOOKUP",
    "GENERAL_QA",
    "LOCATION",
    "PROCEDURE",
    "RULE_LOOKUP",
    "SUMMARY",
    "TABLE_LOOKUP",
    "VALUE_LOOKUP",
}

EXHAUSTIVE_PATTERN = re.compile(
    r"\b(tous|toutes|complete|completee|liste complete|all|every|"
    r"tableau complet|codes|valeurs|values)\b",
    re.IGNORECASE,
)


def evidence_counts(bundle: EvidenceBundle | None) -> dict[str, int]:
    if bundle is None:
        return {
            "definitions": 0,
            "facts": 0,
            "rules": 0,
            "tables": 0,
            "table_rows": 0,
            "examples": 0,
            "process_steps": 0,
            "other_evidence": 0,
            "retrieved_units": 0,
            "citations": 0,
            "evidence_items": 0,
        }

    table_rows = sum(len(table.rows) for table in bundle.tables)
    evidence_items = (
        len(bundle.definitions)
        + len(bundle.facts)
        + len(bundle.rules)
        + len(bundle.examples)
        + len(bundle.process_steps)
        + len(bundle.other_evidence)
    )

    return {
        "definitions": len(bundle.definitions),
        "facts": len(bundle.facts),
        "rules": len(bundle.rules),
        "tables": len(bundle.tables),
        "table_rows": table_rows,
        "examples": len(bundle.examples),
        "process_steps": len(bundle.process_steps),
        "other_evidence": len(bundle.other_evidence),
        "retrieved_units": len(bundle.retrieved_units),
        "citations": len(bundle.citations),
        "evidence_items": evidence_items,
    }


def needs_complete_table(
    *,
    question: str,
    intent: str,
    query_plan: Any | None = None,
) -> bool:
    if bool(getattr(query_plan, "require_complete_table", False)):
        return True

    return intent == "TABLE_LOOKUP" and bool(EXHAUSTIVE_PATTERN.search(question))


class RetrievalEvidenceGuardrail:
    """Validates that retrieval produced enough evidence before generation."""

    @staticmethod
    def validate(
        *,
        question: str,
        intent: str,
        bundle: EvidenceBundle | None,
        retrieval_complete: bool | None = None,
        query_plan: Any | None = None,
    ) -> GuardrailResult:
        counts = evidence_counts(bundle)
        metadata = {
            "intent": intent,
            "retrieval_complete": retrieval_complete,
            **counts,
        }

        if intent not in DOCUMENTARY_INTENTS:
            return GuardrailResult.pass_(
                code="DOCUMENTARY_EVIDENCE_NOT_REQUIRED",
                reason="This intent does not require documentary RAG evidence.",
                metadata=metadata,
            )

        if bundle is None:
            return RetrievalEvidenceGuardrail._block(
                code="MISSING_EVIDENCE_BUNDLE",
                reason="No EvidenceBundle is available for a documentary question.",
                metadata=metadata,
            )

        if retrieval_complete is False:
            return RetrievalEvidenceGuardrail._block(
                code="INCOMPLETE_RETRIEVAL",
                reason="Retrieval marked the evidence as incomplete.",
                metadata=metadata,
            )

        if counts["evidence_items"] == 0 and counts["table_rows"] == 0:
            return RetrievalEvidenceGuardrail._block(
                code="EMPTY_EVIDENCE",
                reason="The EvidenceBundle does not contain usable facts, tables or examples.",
                metadata=metadata,
            )

        if counts["retrieved_units"] == 0 and counts["citations"] == 0:
            return RetrievalEvidenceGuardrail._block(
                code="NO_DOCUMENTARY_PROVENANCE",
                reason="Evidence exists but has no retrieved ContentUnit or citation provenance.",
                metadata=metadata,
            )

        if needs_complete_table(
            question=question,
            intent=intent,
            query_plan=query_plan,
        ) and counts["table_rows"] == 0:
            return RetrievalEvidenceGuardrail._block(
                code="INSUFFICIENT_TABLE_EVIDENCE",
                reason="The question asks for a table/list, but no structured table rows were retrieved.",
                metadata=metadata,
            )

        result = GuardrailResult.pass_(
            code="SUFFICIENT_EVIDENCE",
            reason="EvidenceBundle contains usable documentary evidence.",
            metadata=metadata,
        )
        RetrievalEvidenceGuardrail._log(result)
        return result

    @staticmethod
    def _block(
        *,
        code: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> GuardrailResult:
        result = GuardrailResult.block(
            code=code,
            reason=reason,
            metadata=metadata,
        )
        RetrievalEvidenceGuardrail._log(result)
        return result

    @staticmethod
    def _log(result: GuardrailResult) -> None:
        log = logger.warning if result.status == GuardrailStatus.BLOCK else logger.info
        log(
            "GUARDRAIL retrieval status=%s code=%s reason=%s metadata=%s",
            result.status.value,
            result.code,
            result.reason,
            result.metadata,
        )
