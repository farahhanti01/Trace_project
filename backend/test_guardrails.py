import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.guardrails.models import GuardrailStatus  # noqa: E402
from app.guardrails.output_guard import OutputGuardrail  # noqa: E402
from app.guardrails.retrieval_guard import RetrievalEvidenceGuardrail  # noqa: E402
from app.models.document_content import (  # noqa: E402
    ContentHierarchy,
    ContentUnit,
    EvidenceBundle,
    EvidenceItem,
    EvidenceTable,
    SourceLocation,
)
from app.services.adaptive_retrieval_service import (  # noqa: E402
    AdaptiveRetrievalResult,
    RetrievalCompleteness,
)
from app.services.documentation_evidence_generation_service import (  # noqa: E402
    build_evidence_candidate,
)
from app.services.generic_question_classifier_service import (  # noqa: E402
    ClassificationResult,
    GenericEntities,
    QueryPlan,
)


def sample_unit(unit_id="u-1", content_type="paragraph"):
    return ContentUnit(
        unit_id=unit_id,
        document_id="doc-1",
        hierarchy=ContentHierarchy(section="1.1", title="Sample"),
        content_type=content_type,
        content="Documented technical fact.",
        source_location=SourceLocation(pdf_page=12),
    )


def sample_item(unit):
    return EvidenceItem(
        content=unit.content,
        source_unit_id=unit.unit_id,
        document_id=unit.document_id,
        pdf_page=unit.source_location.pdf_page,
        section=unit.hierarchy.section,
        content_type=unit.content_type,
    )


class GuardrailTests(unittest.TestCase):
    def test_correct_evidence_bundle_passes(self):
        unit = sample_unit()
        bundle = EvidenceBundle(
            query="Qu'est-ce que X ?",
            intent="DEFINITION",
            facts=[sample_item(unit)],
            retrieved_units=[unit],
            citations=[sample_item(unit)],
        )

        result = RetrievalEvidenceGuardrail.validate(
            question="Qu'est-ce que X ?",
            intent="DEFINITION",
            bundle=bundle,
            retrieval_complete=True,
        )

        self.assertEqual(result.status, GuardrailStatus.PASS)

    def test_empty_evidence_bundle_blocks_documentary_question(self):
        bundle = EvidenceBundle(
            query="Que represente le Field 039 ?",
            intent="FIELD_LOOKUP",
        )

        result = RetrievalEvidenceGuardrail.validate(
            question="Que represente le Field 039 ?",
            intent="FIELD_LOOKUP",
            bundle=bundle,
            retrieval_complete=True,
        )

        self.assertEqual(result.status, GuardrailStatus.BLOCK)
        self.assertEqual(result.code, "EMPTY_EVIDENCE")

    def test_missing_documentary_provenance_blocks(self):
        unit = sample_unit()
        bundle = EvidenceBundle(
            query="Qu'est-ce que X ?",
            intent="DEFINITION",
            facts=[sample_item(unit)],
        )

        result = RetrievalEvidenceGuardrail.validate(
            question="Qu'est-ce que X ?",
            intent="DEFINITION",
            bundle=bundle,
            retrieval_complete=True,
        )

        self.assertEqual(result.status, GuardrailStatus.BLOCK)
        self.assertEqual(result.code, "NO_DOCUMENTARY_PROVENANCE")

    def test_non_documentary_intent_does_not_block(self):
        result = RetrievalEvidenceGuardrail.validate(
            question="Quel field analysions-nous avant ?",
            intent="CONVERSATION_RECALL",
            bundle=None,
            retrieval_complete=None,
        )

        self.assertEqual(result.status, GuardrailStatus.PASS)

    def test_complete_table_question_without_rows_blocks(self):
        unit = sample_unit(content_type="field_description")
        bundle = EvidenceBundle(
            query="Quels sont tous les codes du Field 039 ?",
            intent="TABLE_LOOKUP",
            facts=[sample_item(unit)],
            retrieved_units=[unit],
            citations=[sample_item(unit)],
        )

        result = RetrievalEvidenceGuardrail.validate(
            question="Quels sont tous les codes du Field 039 ?",
            intent="TABLE_LOOKUP",
            bundle=bundle,
            retrieval_complete=True,
        )

        self.assertEqual(result.status, GuardrailStatus.BLOCK)
        self.assertEqual(result.code, "INSUFFICIENT_TABLE_EVIDENCE")

    def test_table_with_rows_passes(self):
        unit = sample_unit("row-1", "table_row")
        bundle = EvidenceBundle(
            query="Quels sont tous les codes ?",
            intent="TABLE_LOOKUP",
            tables=[
                EvidenceTable(
                    title="Codes",
                    columns=["code", "meaning"],
                    rows=[{"code": "00", "meaning": "Approved"}],
                    source_unit_ids=["row-1"],
                )
            ],
            retrieved_units=[unit],
            citations=[sample_item(unit)],
        )

        result = RetrievalEvidenceGuardrail.validate(
            question="Quels sont tous les codes ?",
            intent="TABLE_LOOKUP",
            bundle=bundle,
            retrieval_complete=True,
        )

        self.assertEqual(result.status, GuardrailStatus.PASS)

    def test_valid_structured_response_passes(self):
        response = {
            "summary": "Reponse fiable.",
            "sections": [],
            "references": [{"document_id": "doc-1", "source": "Spec.pdf"}],
        }

        result = OutputGuardrail.validate(
            response=response,
            documentary_evidence_used=True,
            known_document_ids={"doc-1"},
        )

        self.assertEqual(result.status, GuardrailStatus.PASS)

    def test_empty_summary_blocks_output(self):
        result = OutputGuardrail.validate(
            response={"summary": "", "sections": [], "references": []},
            documentary_evidence_used=True,
        )

        self.assertEqual(result.status, GuardrailStatus.BLOCK)
        self.assertEqual(result.code, "EMPTY_SUMMARY")

    def test_unknown_reference_document_warns(self):
        response = {
            "summary": "Reponse avec source.",
            "references": [{"document_id": "missing-doc"}],
        }

        result = OutputGuardrail.validate(
            response=response,
            documentary_evidence_used=True,
            known_document_ids={"doc-1"},
        )

        self.assertEqual(result.status, GuardrailStatus.WARNING)
        self.assertEqual(result.code, "UNKNOWN_REFERENCE_DOCUMENT")

    def test_reference_absent_from_evidence_warns(self):
        unit = sample_unit("u-known")
        bundle = EvidenceBundle(
            query="Qu'est-ce que X ?",
            intent="DEFINITION",
            facts=[sample_item(unit)],
            retrieved_units=[unit],
            citations=[sample_item(unit)],
        )
        response = {
            "summary": "Reponse avec source inventee.",
            "references": [{"source_id": "u-invented", "source": "Spec.pdf"}],
        }

        result = OutputGuardrail.validate(
            response=response,
            documentary_evidence_used=True,
            evidence_bundle=bundle,
        )

        self.assertEqual(result.status, GuardrailStatus.WARNING)
        self.assertEqual(result.code, "UNSUPPORTED_REFERENCE_SOURCE")

    def test_reference_page_mismatch_warns(self):
        unit = sample_unit("u-page")
        bundle = EvidenceBundle(
            query="Qu'est-ce que X ?",
            intent="DEFINITION",
            facts=[sample_item(unit)],
            retrieved_units=[unit],
            citations=[sample_item(unit)],
        )
        response = {
            "summary": "Reponse avec mauvaise page.",
            "references": [{"source_id": "u-page", "source": "Spec.pdf", "pdf_page": 99}],
        }

        result = OutputGuardrail.validate(
            response=response,
            documentary_evidence_used=True,
            evidence_bundle=bundle,
        )

        self.assertEqual(result.status, GuardrailStatus.WARNING)
        self.assertEqual(result.code, "INCONSISTENT_REFERENCE_PAGE")

    def test_references_without_documentary_evidence_warn(self):
        response = {
            "summary": "Extraction depuis image.",
            "references": [{"source": "Spec.pdf"}],
        }

        result = OutputGuardrail.validate(
            response=response,
            documentary_evidence_used=False,
        )

        self.assertEqual(result.status, GuardrailStatus.WARNING)
        self.assertEqual(
            result.code,
            "REFERENCES_WITHOUT_DOCUMENTARY_EVIDENCE",
        )


class GuardrailPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrieval_block_prevents_writer_call(self):
        writer = AsyncMock(return_value={"summary": "Should not be called"})

        with patch(
            "app.services.documentation_evidence_generation_service.GenericQuestionClassifier.classify",
            return_value=ClassificationResult(
                intent="FIELD_LOOKUP",
                confidence=1.0,
                entities=GenericEntities(field_numbers=["039"]),
            ),
        ), patch(
            "app.services.documentation_evidence_generation_service.QueryPlanner.build",
            return_value=QueryPlan(intent="FIELD_LOOKUP"),
        ), patch(
            "app.services.documentation_evidence_generation_service.AdaptiveRetriever.retrieve",
            new=AsyncMock(
                return_value=AdaptiveRetrievalResult(
                    sections=[],
                    content_units=[],
                    completeness=RetrievalCompleteness(retrieval_complete=True),
                )
            ),
        ), patch(
            "app.services.documentation_evidence_generation_service.filter_units_for_entity_consistency",
            return_value=([], {}),
        ), patch(
            "app.services.documentation_evidence_generation_service.check_evidence_completeness",
            return_value=[],
        ), patch(
            "app.services.documentation_evidence_generation_service.EvidenceBuilder.build",
            return_value={
                "query": "Que represente le Field 039 ?",
                "intent": "FIELD_LOOKUP",
                "entities": {"field_number": "039"},
            },
        ), patch(
            "app.services.documentation_evidence_generation_service.EvidenceResponseWriter.generate",
            new=writer,
        ):
            candidate = await build_evidence_candidate(
                question="Que represente le Field 039 ?",
                sections=[{"document_id": "doc-1"}],
            )

        writer.assert_not_called()
        self.assertFalse(candidate["candidate_valid"])
        self.assertEqual(candidate["fallback_reason"], "EMPTY_EVIDENCE")
        self.assertIsNone(candidate["response"])

    async def test_output_block_removes_candidate_response(self):
        unit = sample_unit("u-1")
        writer = AsyncMock(
            return_value={
                "summary": "",
                "sections": [],
                "references": [],
            }
        )

        with patch(
            "app.services.documentation_evidence_generation_service.GenericQuestionClassifier.classify",
            return_value=ClassificationResult(
                intent="DEFINITION",
                confidence=1.0,
                entities=GenericEntities(concepts=["STIP"]),
            ),
        ), patch(
            "app.services.documentation_evidence_generation_service.QueryPlanner.build",
            return_value=QueryPlan(intent="DEFINITION"),
        ), patch(
            "app.services.documentation_evidence_generation_service.AdaptiveRetriever.retrieve",
            new=AsyncMock(
                return_value=AdaptiveRetrievalResult(
                    sections=[{"document_id": "doc-1"}],
                    content_units=[unit.model_dump()],
                    completeness=RetrievalCompleteness(retrieval_complete=True),
                )
            ),
        ), patch(
            "app.services.documentation_evidence_generation_service.filter_units_for_entity_consistency",
            return_value=([unit], {}),
        ), patch(
            "app.services.documentation_evidence_generation_service.check_evidence_completeness",
            return_value=[],
        ), patch(
            "app.services.documentation_evidence_generation_service.EvidenceBuilder.build",
            return_value={
                "query": "Qu'est-ce que STIP ?",
                "intent": "DEFINITION",
                "entities": {"concept": "STIP"},
                "facts": [sample_item(unit).model_dump()],
                "retrieved_units": [unit.model_dump()],
                "citations": [sample_item(unit).model_dump()],
            },
        ), patch(
            "app.services.documentation_evidence_generation_service.EvidenceResponseWriter.generate",
            new=writer,
        ):
            candidate = await build_evidence_candidate(
                question="Qu'est-ce que STIP ?",
                sections=[{"document_id": "doc-1"}],
            )

        writer.assert_awaited()
        self.assertFalse(candidate["candidate_valid"])
        self.assertIsNone(candidate["response"])
        self.assertIsNotNone(candidate["blocked_response"])
        self.assertEqual(
            candidate["guardrails"]["output"]["status"],
            GuardrailStatus.BLOCK,
        )


if __name__ == "__main__":
    unittest.main()
