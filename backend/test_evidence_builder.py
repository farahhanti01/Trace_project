import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.models.document_content import (
    ContentHierarchy,
    ContentUnit,
    SourceLocation,
)
from app.services.evidence_builder_service import (
    EvidenceBuilder,
    classify_documentation_question,
    extract_question_entities,
)


def unit(
    content_type,
    content,
    *,
    unit_id,
    field_number=None,
    code=None,
    title="General section",
    parent_unit_id=None,
    order=1,
):
    entities = {}

    if field_number:
        entities["field_number"] = field_number

    if code:
        entities["code"] = code

    return ContentUnit(
        unit_id=unit_id,
        document_id="doc-1",
        conversation_id="conversation-1",
        source="technical-spec.pdf",
        hierarchy=ContentHierarchy(
            chapter="Chapter 1",
            section="1.1",
            title=title,
        ),
        content_type=content_type,
        entities=entities,
        content=content,
        raw_text=content,
        source_location=SourceLocation(
            pdf_page=10,
            printed_page="1-1",
        ),
        parent_unit_id=parent_unit_id,
        document_order=order,
        parsing_confidence=0.9,
    ).model_dump()


class EvidenceBuilderTests(unittest.TestCase):
    def test_field_lookup_entities_are_generic(self):
        entities = extract_question_entities("Que represente Field 039 ?")

        self.assertEqual(entities["field_number"], "039")

    def test_value_lookup_entities_capture_code(self):
        entities = extract_question_entities("Que signifie 039=51 ?")

        self.assertEqual(entities["field_number"], "039")
        self.assertEqual(entities["code"], "51")

    def test_question_classifier_is_not_field_only(self):
        self.assertEqual(
            classify_documentation_question("Explique le message matching."),
            "EXPLANATION",
        )
        self.assertEqual(
            classify_documentation_question("Comment fonctionne un reversal ?"),
            "PROCEDURE",
        )
        self.assertEqual(
            classify_documentation_question("Qu'est-ce que STIP ?"),
            "DEFINITION",
        )
        self.assertEqual(
            classify_documentation_question("Resume le chapitre 2."),
            "SUMMARY",
        )
        self.assertEqual(
            classify_documentation_question("Quelle difference entre 0100 et 0200 ?"),
            "COMPARISON",
        )

    def test_field_lookup_builds_definitions_and_facts(self):
        bundle = EvidenceBuilder.build(
            question="Que represente Field 039 ?",
            content_units=[
                unit(
                    "field_description",
                    "Field 039 contains response codes.",
                    unit_id="u1",
                    field_number="039",
                    order=1,
                ),
                unit(
                    "field_usage",
                    "It is used in response messages.",
                    unit_id="u2",
                    field_number="039",
                    order=2,
                ),
            ],
        )

        self.assertEqual(bundle.intent, "FIELD_LOOKUP")
        self.assertEqual(len(bundle.definitions), 1)
        self.assertEqual(len(bundle.facts), 1)
        self.assertEqual(bundle.citations[0].source_unit_id, "u1")

    def test_code_mapping_enters_facts_for_value_lookup(self):
        bundle = EvidenceBuilder.build(
            question="Que signifie 039=51 ?",
            content_units=[
                unit(
                    "code_mapping",
                    "Insufficient funds",
                    unit_id="u1",
                    field_number="039",
                    code="51",
                ),
                unit(
                    "code_mapping",
                    "Expired card",
                    unit_id="u2",
                    field_number="039",
                    code="54",
                ),
            ],
        )

        self.assertEqual(bundle.intent, "VALUE_LOOKUP")
        self.assertEqual(len(bundle.facts), 1)
        self.assertEqual(bundle.facts[0].content, "Insufficient funds")

    def test_table_units_are_grouped(self):
        bundle = EvidenceBuilder.build(
            question="Quels sont les reject codes de cette section ?",
            content_units=[
                unit(
                    "table",
                    "Reject codes",
                    unit_id="table-1",
                    title="Reject codes",
                    order=1,
                ),
                unit(
                    "table_row",
                    "0306 | Missing field",
                    unit_id="row-1",
                    parent_unit_id="table-1",
                    order=2,
                ),
            ],
        )

        self.assertEqual(bundle.intent, "VALUE_LOOKUP")
        self.assertEqual(len(bundle.tables), 1)
        self.assertEqual(bundle.tables[0].rows[0]["col_1"], "0306")

    def test_other_evidence_keeps_unclassified_useful_units(self):
        bundle = EvidenceBuilder.build(
            question="Explique le systeme.",
            content_units=[
                unit(
                    "unknown",
                    "A useful but unclassified technical detail.",
                    unit_id="u1",
                )
            ],
        )

        self.assertEqual(len(bundle.other_evidence), 1)


if __name__ == "__main__":
    unittest.main()
