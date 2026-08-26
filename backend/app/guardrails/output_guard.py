import logging
from typing import Any

from app.guardrails.models import GuardrailResult, GuardrailStatus
from app.models.document_content import EvidenceBundle


logger = logging.getLogger(__name__)


class OutputGuardrail:
    """Minimal structural checks for backend responses sent to the UI."""

    @staticmethod
    def validate(
        *,
        response: dict[str, Any] | None,
        documentary_evidence_used: bool = False,
        known_document_ids: set[str] | None = None,
        evidence_bundle: EvidenceBundle | None = None,
    ) -> GuardrailResult:
        if not isinstance(response, dict):
            return OutputGuardrail._block(
                code="INVALID_RESPONSE_OBJECT",
                reason="Structured response must be a dictionary.",
                metadata={},
            )

        summary = response.get("summary")
        metadata = {
            "has_summary": bool(str(summary or "").strip()),
            "sections": len(response.get("sections") or []),
            "references": len(response.get("references") or []),
            "documentary_evidence_used": documentary_evidence_used,
        }

        if not str(summary or "").strip():
            return OutputGuardrail._block(
                code="EMPTY_SUMMARY",
                reason="Structured response has an empty summary.",
                metadata=metadata,
            )

        references = response.get("references") or []

        if not isinstance(references, list):
            return OutputGuardrail._block(
                code="INVALID_REFERENCES",
                reason="Structured response references must be a list.",
                metadata=metadata,
            )

        if references and not documentary_evidence_used:
            return OutputGuardrail._warning(
                code="REFERENCES_WITHOUT_DOCUMENTARY_EVIDENCE",
                reason="Response exposes references while no documentary evidence was used.",
                metadata=metadata,
            )

        unknown_documents = []
        unsupported_source_ids = []
        inconsistent_pages = []

        evidence_source_ids, evidence_document_ids, evidence_pages_by_source_id = (
            OutputGuardrail._evidence_reference_index(evidence_bundle)
        )
        effective_document_ids = set(known_document_ids or set())
        effective_document_ids.update(evidence_document_ids)

        if effective_document_ids:
            for reference in references:
                if not isinstance(reference, dict):
                    continue

                document_id = reference.get("document_id")

                if document_id and str(document_id) not in effective_document_ids:
                    unknown_documents.append(str(document_id))

                source_id = reference.get("source_id")

                if source_id and evidence_source_ids and source_id not in evidence_source_ids:
                    unsupported_source_ids.append(str(source_id))
                    continue

                pdf_page = reference.get("pdf_page") or reference.get("page")
                expected_page = evidence_pages_by_source_id.get(source_id)

                if (
                    source_id
                    and expected_page is not None
                    and pdf_page is not None
                    and OutputGuardrail._safe_int(pdf_page)
                    != OutputGuardrail._safe_int(expected_page)
                ):
                    inconsistent_pages.append({
                        "source_id": str(source_id),
                        "expected_pdf_page": expected_page,
                        "observed_pdf_page": pdf_page,
                    })

        if unsupported_source_ids:
            return OutputGuardrail._warning(
                code="UNSUPPORTED_REFERENCE_SOURCE",
                reason="One or more references point to source_ids absent from EvidenceBundle.",
                metadata={
                    **metadata,
                    "unsupported_source_ids": sorted(set(unsupported_source_ids)),
                },
            )

        if unknown_documents:
            return OutputGuardrail._warning(
                code="UNKNOWN_REFERENCE_DOCUMENT",
                reason="One or more references point to documents outside the known evidence set.",
                metadata={
                    **metadata,
                    "unknown_document_ids": sorted(set(unknown_documents)),
                },
            )

        if inconsistent_pages:
            return OutputGuardrail._warning(
                code="INCONSISTENT_REFERENCE_PAGE",
                reason="One or more references use a page that differs from EvidenceBundle provenance.",
                metadata={
                    **metadata,
                    "inconsistent_pages": inconsistent_pages,
                },
            )

        result = GuardrailResult.pass_(
            code="VALID_STRUCTURED_RESPONSE",
            reason="Structured response passed minimal output checks.",
            metadata=metadata,
        )
        OutputGuardrail._log(result)
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
        OutputGuardrail._log(result)
        return result

    @staticmethod
    def _warning(
        *,
        code: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> GuardrailResult:
        result = GuardrailResult.warning(
            code=code,
            reason=reason,
            metadata=metadata,
        )
        OutputGuardrail._log(result)
        return result

    @staticmethod
    def _log(result: GuardrailResult) -> None:
        log = logger.warning if result.status != GuardrailStatus.PASS else logger.info
        log(
            "GUARDRAIL output status=%s code=%s reason=%s metadata=%s",
            result.status.value,
            result.code,
            result.reason,
            result.metadata,
        )

    @staticmethod
    def _evidence_reference_index(
        evidence_bundle: EvidenceBundle | None,
    ) -> tuple[set[str], set[str], dict[str, int | None]]:
        if evidence_bundle is None:
            return set(), set(), {}

        source_ids = set()
        document_ids = set()
        pages_by_source_id: dict[str, int | None] = {}
        evidence_items = [
            *evidence_bundle.definitions,
            *evidence_bundle.facts,
            *evidence_bundle.rules,
            *evidence_bundle.examples,
            *evidence_bundle.process_steps,
            *evidence_bundle.other_evidence,
            *evidence_bundle.citations,
        ]

        for item in evidence_items:
            if item.source_unit_id:
                source_ids.add(item.source_unit_id)
                pages_by_source_id[item.source_unit_id] = item.pdf_page

            if item.document_id:
                document_ids.add(str(item.document_id))

        for unit in evidence_bundle.retrieved_units:
            if unit.unit_id:
                source_ids.add(unit.unit_id)
                pages_by_source_id.setdefault(
                    unit.unit_id,
                    unit.source_location.pdf_page,
                )

            if unit.document_id:
                document_ids.add(str(unit.document_id))

        for table in evidence_bundle.tables:
            for source_id in table.source_unit_ids:
                if source_id:
                    source_ids.add(source_id)

        return source_ids, document_ids, pages_by_source_id

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
