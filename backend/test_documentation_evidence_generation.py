import os
import sys
import unittest
import json
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.document_content import (  # noqa: E402
    ContentHierarchy,
    ContentUnit,
    EvidenceBundle,
    EvidenceItem,
    EvidenceTable,
    SourceLocation,
)
from app.services.documentation_evidence_generation_service import (  # noqa: E402
    AnswerRequirements,
    build_table_lookup_response,
    build_value_lookup_response,
    evidence_generation_enabled,
    evidence_pipeline_allowed,
    extract_answer_requirements,
    normalize_writer_payload,
    validate_evidence_response,
)
from app.services.generic_question_classifier_service import (  # noqa: E402
    GenericQuestionClassifier,
)


def unit(
    unit_id,
    content_type,
    content,
    *,
    field_number=None,
    code=None,
    parent_unit_id=None,
):
    entities = {}

    if field_number:
        entities["field_number"] = field_number

    if code:
        entities["code"] = code

    return ContentUnit(
        unit_id=unit_id,
        document_id="doc-1",
        source="BASE I Technical Specifications, Volume 1",
        hierarchy=ContentHierarchy(
            chapter="Chapter 4",
            section="4.27",
            title="Field 39 Response Code",
        ),
        content_type=content_type,
        entities=entities,
        content=content,
        raw_text=content,
        source_location=SourceLocation(
            pdf_page=185,
            printed_page="4-82",
        ),
        parent_unit_id=parent_unit_id,
        parsing_confidence=0.98,
    )


def item(source_unit):
    return EvidenceItem(
        content=source_unit.content,
        source_unit_id=source_unit.unit_id,
        document_id=source_unit.document_id,
        pdf_page=source_unit.source_location.pdf_page,
        printed_page=source_unit.source_location.printed_page,
        section=source_unit.hierarchy.section,
        content_type=source_unit.content_type,
        confidence=source_unit.parsing_confidence,
    )


class DocumentationEvidenceGenerationTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("DOCUMENTATION_EVIDENCE_GENERATION", None)

    def test_generation_flag_is_disabled_by_default(self):
        os.environ.pop("DOCUMENTATION_EVIDENCE_GENERATION", None)

        self.assertFalse(evidence_generation_enabled())

    def test_allowed_intents_are_limited_for_v1(self):
        self.assertTrue(evidence_pipeline_allowed("VALUE_LOOKUP"))
        self.assertTrue(evidence_pipeline_allowed("TABLE_LOOKUP"))
        self.assertTrue(evidence_pipeline_allowed("FIELD_LOOKUP"))
        self.assertTrue(evidence_pipeline_allowed("DEFINITION"))
        self.assertFalse(evidence_pipeline_allowed("COMPARISON"))
        self.assertFalse(evidence_pipeline_allowed("SUMMARY"))

    def test_value_lookup_uses_only_requested_mapping(self):
        unit_51 = unit(
            "u-51",
            "code_mapping",
            "Insufficient funds",
            field_number="039",
            code="51",
        )
        unit_55 = unit(
            "u-55",
            "code_mapping",
            "Incorrect PIN",
            field_number="039",
            code="55",
        )
        bundle = EvidenceBundle(
            query="Que signifie 039=51 ?",
            intent="VALUE_LOOKUP",
            entities={"field_number": "039", "code": "51"},
            facts=[item(unit_51), item(unit_55)],
            retrieved_units=[unit_51, unit_55],
            citations=[item(unit_51), item(unit_55)],
        )
        classification = GenericQuestionClassifier.classify(
            "Que signifie 039=51 ?"
        )
        response = build_value_lookup_response(
            "Que signifie 039=51 ?",
            classification,
            bundle,
        )

        self.assertIsNotNone(response)
        self.assertIn("Insufficient funds", response["summary"])
        self.assertNotIn("Incorrect PIN", response["summary"])
        self.assertEqual(len(response["references"]), 1)

    def test_table_lookup_preserves_structured_rows(self):
        table_unit = unit(
            "table-1",
            "table",
            "Field 39 Response Codes",
            field_number="039",
        )
        row_00 = unit(
            "row-00",
            "table_row",
            "00 | Approval",
            field_number="039",
            parent_unit_id="table-1",
        )
        row_51 = unit(
            "row-51",
            "table_row",
            "51 | Insufficient funds",
            field_number="039",
            parent_unit_id="table-1",
        )
        bundle = EvidenceBundle(
            query="Quels sont les codes du Field 039 ?",
            intent="TABLE_LOOKUP",
            entities={"field_number": "039"},
            facts=[item(row_00), item(row_51)],
            tables=[
                EvidenceTable(
                    title="Field 39 Response Codes",
                    columns=["col_1", "col_2"],
                    rows=[
                        {
                            "col_1": "00",
                            "col_2": "Approval",
                            "source_unit_id": "row-00",
                        },
                        {
                            "col_1": "51",
                            "col_2": "Insufficient funds",
                            "source_unit_id": "row-51",
                        },
                    ],
                    source_unit_ids=["table-1", "row-00", "row-51"],
                )
            ],
            retrieved_units=[table_unit, row_00, row_51],
            citations=[item(row_00), item(row_51)],
        )
        response = build_table_lookup_response(
            "Quels sont les codes du Field 039 ?",
            bundle,
        )

        self.assertIsNotNone(response)
        table_block = response["sections"][0]["blocks"][0]

        self.assertEqual(table_block["rows"][0]["code"], "00")
        self.assertEqual(table_block["rows"][1]["code"], "51")
        self.assertEqual(
            table_block["rows"][1]["meaning"],
            "Insufficient funds",
        )

    def test_all_codes_question_requires_complete_table(self):
        classification = GenericQuestionClassifier.classify(
            "Donne-moi tous les codes du Field 039"
        )
        requirements = extract_answer_requirements(
            "Donne-moi tous les codes du Field 039",
            classification,
        )

        self.assertEqual(classification.intent, "TABLE_LOOKUP")
        self.assertTrue(requirements.table)
        self.assertTrue(requirements.all_values)
        self.assertTrue(requirements.values)

    def test_table_lookup_preserves_generic_table_columns(self):
        table_unit = unit(
            "table-command",
            "table",
            "Command Response Meaning",
        )
        row = unit(
            "row-command",
            "table_row",
            "EC | ED | PIN verification",
            parent_unit_id="table-command",
        )
        bundle = EvidenceBundle(
            query="Quels sont tous les codes de cette table ?",
            intent="TABLE_LOOKUP",
            tables=[
                EvidenceTable(
                    title="Command Response Meaning",
                    columns=["command", "response", "meaning"],
                    rows=[
                        {
                            "command": "EC",
                            "response": "ED",
                            "meaning": "PIN verification",
                            "source_unit_id": "row-command",
                        }
                    ],
                    source_unit_ids=["table-command", "row-command"],
                )
            ],
            retrieved_units=[table_unit, row],
            facts=[item(row)],
            citations=[item(row)],
        )

        response = build_table_lookup_response(
            "Quels sont tous les codes de cette table ?",
            bundle,
            AnswerRequirements(table=True, all_values=True, values=True),
        )

        self.assertIsNotNone(response)
        table_block = response["sections"][0]["blocks"][0]
        self.assertEqual(
            [column["key"] for column in table_block["columns"][:3]],
            ["command", "response", "meaning"],
        )
        self.assertEqual(table_block["rows"][0]["command"], "EC")
        self.assertEqual(table_block["rows"][0]["response"], "ED")
        self.assertEqual(table_block["rows"][0]["meaning"], "PIN verification")

    def test_requirements_capture_complex_field_question(self):
        classification = GenericQuestionClassifier.classify(
            "Explique-moi le role du Field 039 dans une transaction. "
            "Indique sa structure, les principaux codes, puis donne un "
            "exemple et cite les pages."
        )
        requirements = extract_answer_requirements(
            "Explique-moi le role du Field 039 dans une transaction. "
            "Indique sa structure, les principaux codes, puis donne un "
            "exemple et cite les pages.",
            classification,
        )

        self.assertTrue(requirements.role)
        self.assertTrue(requirements.structure)
        self.assertTrue(requirements.trace_usage)
        self.assertTrue(requirements.values)
        self.assertTrue(requirements.example)
        self.assertTrue(requirements.citations)

    def test_evidence_writer_unwraps_json_summary(self):
        source_unit = unit(
            "u-39",
            "field_description",
            "Field 39 contains a response code.",
            field_number="039",
        )
        nested = {
            "summary": "Le Field 039 contient le code de reponse.",
            "sections": [
                {
                    "title": "Structure",
                    "content": "Champ de deux caracteres.",
                    "source_ids": ["u-39"],
                }
            ],
        }
        bundle = EvidenceBundle(
            query="Que represente Field 039 ?",
            intent="FIELD_LOOKUP",
            entities={"field_number": "039"},
            facts=[item(source_unit)],
            retrieved_units=[source_unit],
            citations=[item(source_unit)],
        )

        response = normalize_writer_payload(
            {
                "summary": json.dumps(nested),
                "sections": [],
            },
            bundle,
        )

        self.assertEqual(
            response["summary"],
            "Le Field 039 contient le code de reponse.",
        )
        self.assertEqual(response["sections"][0]["title"], "Structure")

    def test_validator_detects_code_mapping_mismatch(self):
        unit_10 = unit(
            "u-10",
            "code_mapping",
            "Partial approval",
            field_number="039",
            code="10",
        )
        bundle = EvidenceBundle(
            query="Quels sont les codes du Field 039 ?",
            intent="TABLE_LOOKUP",
            entities={"field_number": "039"},
            facts=[item(unit_10)],
            retrieved_units=[unit_10],
            citations=[item(unit_10)],
        )
        response = {
            "summary": "Codes du Field 039.",
            "sections": [
                {
                    "title": "Valeurs et significations",
                    "content": "",
                    "source_ids": ["u-10"],
                    "blocks": [
                        {
                            "type": "table",
                            "rows": [
                                {
                                    "code": "10",
                                    "meaning": "Acceptation",
                                    "evidence_id": "E1",
                                }
                            ],
                        }
                    ],
                }
            ],
            "references": [
                {
                    "source_id": "u-10",
                    "source": "technical-spec.pdf",
                    "pdf_page": 185,
                }
            ],
        }
        validation = validate_evidence_response(
            response=response,
            requirements=AnswerRequirements(values=True, citations=True),
            bundle=bundle,
        )

        self.assertFalse(validation.valid)
        self.assertEqual(validation.fact_mismatches[0]["code"], "10")

    def test_validator_rejects_table_lookup_without_table(self):
        bundle = EvidenceBundle(
            query="Donne-moi tous les codes du Field 039",
            intent="TABLE_LOOKUP",
        )
        response = {
            "summary": "Le Field 039 contient plusieurs codes.",
            "sections": [
                {
                    "title": "Analyse",
                    "content": "Le Field 039 contient divers codes.",
                }
            ],
            "references": [],
        }
        validation = validate_evidence_response(
            response=response,
            requirements=AnswerRequirements(
                values=True,
                table=True,
                all_values=True,
                citations=False,
            ),
            bundle=bundle,
        )

        self.assertFalse(validation.valid)
        self.assertIn("table", validation.missing_requirements)
        self.assertIn("all_values", validation.missing_requirements)


if __name__ == "__main__":
    unittest.main()
