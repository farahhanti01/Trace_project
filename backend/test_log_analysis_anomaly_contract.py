import sys
import unittest
import asyncio
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.log_analysis_agent_service import (
    build_pdf_documentation_findings,
    build_pdf_field_length_rules,
    build_pdf_length_documentation_findings,
    build_hsm_documentation_findings,
    display_options_for_question,
    extracted_trace_document_query,
    extract_hsm_return_code_meaning,
    filter_documented_log_story,
    find_hsm_return_code_meaning_section,
    is_hsm_reference_section,
    observed_pdf_anomalies,
    response_transactions,
    visible_response_transactions,
)


class LogAnalysisAnomalyContractTests(unittest.TestCase):
    def test_generic_trace_analysis_displays_log_story_and_hsm(self):
        options = display_options_for_question("Analyse cette trace.")

        self.assertTrue(options["show_log_story"])
        self.assertTrue(options["show_hsm"])
        self.assertEqual(options["analysis_mode"], "total")

    def test_trace_analysis_request_displays_hsm_like_complete_analysis(self):
        options = display_options_for_question("Je veux une analyse de trace.")

        self.assertTrue(options["show_log_story"])
        self.assertTrue(options["show_hsm"])
        self.assertEqual(options["analysis_mode"], "total")

    def test_natural_trace_analysis_variants_display_hsm(self):
        for question in (
            "Je veux une analyse de la trace.",
            "Je veux l analyse de la trace.",
            "Analyse de la trace.",
            "Peux-tu analyser ce log ?",
        ):
            with self.subTest(question=question):
                options = display_options_for_question(question)
                self.assertTrue(options["show_log_story"])
                self.assertTrue(options["show_hsm"])
                self.assertEqual(options["analysis_mode"], "total")

    def test_hsm_only_request_stays_hsm_focused(self):
        options = display_options_for_question(
            "Analyse uniquement les traitements HSM de cette trace."
        )

        self.assertFalse(options["show_fields"])
        self.assertFalse(options["show_log_story"])
        self.assertTrue(options["show_hsm"])
        self.assertEqual(options["analysis_mode"], "hsm")

    def test_referenced_trace_query_does_not_require_log_agent(self):
        query = extracted_trace_document_query("conversation-1")

        self.assertEqual(query["conversation_id"], "conversation-1")
        self.assertEqual(query["status"], "extracted")
        self.assertNotIn("agent", query)
        self.assertIn("$or", query)

    def test_response_transactions_hide_empty_or_merged_blocks(self):
        transactions = [
            {
                "transaction_id": "request",
                "mti": "0100",
                "status": "FAILED",
                "log_story": [
                    {
                        "function_name": "GetService",
                        "status": "OK",
                    }
                ],
                "hsm_analysis": {},
            },
            {
                "transaction_id": "response",
                "mti": "1110",
                "status": "FAILED",
                "log_story": [],
                "hsm_analysis": {},
                "merged_into_transaction_id": "request",
            },
            {
                "transaction_id": "empty",
                "mti": "0110",
                "status": "SUCCESS",
                "log_story": [],
                "hsm_analysis": {},
            },
        ]

        selected = response_transactions(
            transactions=transactions,
            question="Analyse cette trace.",
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["transaction_id"], "request")

    def test_visible_transactions_group_same_rrn_analysis(self):
        transactions = [
            {
                "transaction_id": "request",
                "log_index": 1,
                "mti": "0100",
                "status": "FAILED",
                "fields": {
                    "037": "601318105410",
                    "039": "55",
                },
                "log_story": [
                    {
                        "order": 1,
                        "function_name": "GetService",
                        "status": "OK",
                    }
                ],
                "hsm_analysis": {
                    "commands": [],
                    "result_codes": [],
                },
                "documentation_findings": [
                    {
                        "type": "length_non_conformity",
                        "field": "039",
                    }
                ],
                "sources": [],
                "evidence": [],
            },
            {
                "transaction_id": "hsm-block",
                "log_index": 2,
                "mti": "1100",
                "status": "FAILED",
                "fields": {
                    "037": "601318105410",
                    "039": "55",
                },
                "log_story": [
                    {
                        "order": 1,
                        "function_name": "CheckSecurity",
                        "status": "ERROR",
                    }
                ],
                "hsm_analysis": {
                    "thread": "00112634",
                    "commands": [
                        {
                            "command": "EC",
                            "response_command": "ED",
                            "hsm_result_code": "ED01",
                            "return_code": "01",
                        }
                    ],
                    "result_codes": ["ED01"],
                },
                "documentation_findings": [],
                "sources": [],
                "evidence": [],
            },
        ]

        selected = visible_response_transactions(transactions)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["transaction_id"], "group_601318105410")
        self.assertEqual(selected[0]["fields"]["037"], "601318105410")
        self.assertEqual(selected[0]["fields"]["039"], "55")
        self.assertEqual(selected[0]["related_mtis"], ["0100", "1100"])
        self.assertEqual(len(selected[0]["log_story"]), 2)
        self.assertEqual(
            len(selected[0]["hsm_analysis"]["commands"]),
            1,
        )
        self.assertEqual(len(selected[0]["documentation_findings"]), 1)

    def test_log_story_keeps_functions_missing_from_excel(self):
        transaction = {
            "log_story": [
                {
                    "function_name": "GetService",
                    "status": "OK",
                },
                {
                    "function_name": "CustomTraceFunction",
                    "status": "OK",
                },
            ],
        }

        filter_documented_log_story(
            transaction=transaction,
            documented_names={"getservice"},
        )

        self.assertEqual(
            [item["function_name"] for item in transaction["log_story"]],
            ["GetService", "CustomTraceFunction"],
        )
        self.assertTrue(transaction["log_story"][0]["documented_in_excel"])
        self.assertFalse(transaction["log_story"][1]["documented_in_excel"])

    def test_non_approved_response_code_is_not_pdf_anomaly_by_itself(self):
        transaction = {
            "mti": "0110",
            "fields": {
                "039": "05",
            },
        }

        self.assertEqual(observed_pdf_anomalies(transaction), [])

    def test_missing_request_field_is_not_pdf_anomaly_by_itself(self):
        transaction = {
            "mti": "0100",
            "fields": {
                "002": "******",
                "037": "432915275372",
            },
        }

        self.assertEqual(observed_pdf_anomalies(transaction), [])

    def test_missing_response_field_requires_explicit_pdf_rule(self):
        transaction = {
            "mti": "0110",
            "fields": {},
        }

        vague_sections = [
            {
                "source": "spec.pdf",
                "text": "Field 39 contains the response code.",
                "page": 185,
                "heading": "Field 39",
            }
        ]

        explicit_sections = [
            {
                "source": "spec.pdf",
                "text": (
                    "Field 39 is required in all 0110 response messages."
                ),
                "page": 185,
                "heading": "Field 39",
            }
        ]

        self.assertEqual(
            build_pdf_documentation_findings(
                transaction=transaction,
                selected_pdf_sections=vague_sections,
                query="analyse",
            ),
            [],
        )

        findings = build_pdf_documentation_findings(
            transaction=transaction,
            selected_pdf_sections=explicit_sections,
            query="analyse",
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["field"], "039")
        self.assertEqual(
            findings[0]["conclusion"],
            "Anomalie demontree par la documentation",
        )

    def test_field_length_non_conformity_requires_pdf_fixed_length_rule(self):
        pdf_sections = [
            {
                "source": "vip-system-BASE-i-tech-specs-volume-1.pdf",
                "document_id": "doc-1",
                "heading": "4.27 Field 39—Response Code",
                "page": 185,
                "page_document": "4-82",
                "text": (
                    "4.27.1 Attributes fixed length 2 AN, EBCDIC; "
                    "2 bytes. Field 39 contains a code that defines "
                    "the response to a request."
                ),
            }
        ]
        transaction = {
            "mti": "0110",
            "fields": {
                "039": "123456",
            },
        }

        rules = build_pdf_field_length_rules(pdf_sections)
        findings = build_pdf_length_documentation_findings(
            transaction=transaction,
            field_length_rules=rules,
        )

        self.assertIn("039", rules)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["field"], "039")
        self.assertEqual(findings[0]["type"], "length_non_conformity")
        self.assertIn("Longueur fixe 2", findings[0]["expected_condition"])
        self.assertEqual(
            findings[0]["conclusion"],
            "Anomalie demontree par la documentation",
        )

    def test_masked_field_value_is_not_checked_for_length(self):
        pdf_sections = [
            {
                "source": "vip-system-BASE-i-tech-specs-volume-1.pdf",
                "heading": "Field 2—Primary Account Number",
                "page": 100,
                "text": "Attributes fixed length 16 N. Field 2 contains PAN.",
            }
        ]
        transaction = {
            "mti": "0100",
            "fields": {
                "002": "******",
            },
        }

        findings = build_pdf_length_documentation_findings(
            transaction=transaction,
            field_length_rules=build_pdf_field_length_rules(pdf_sections),
        )

        self.assertEqual(findings, [])

    def test_hsm_result_code_meaning_comes_from_hsm_document(self):
        hsm_section = {
            "source": "PUGD0537-004 Core Host Commands V1.pdf",
            "heading": "EC Command / ED Response Message",
            "page": 42,
            "text": (
                "RESPONSE MESSAGE Response Code Value 'ED' Error Code "
                "'00': No error '01': PIN verification failure "
                "'10': ZPK parity error"
            ),
        }
        visa_section = {
            "source": "vip-system-BASE-i-tech-specs-volume-1.pdf",
            "heading": "Field 39-Response Code",
            "page": 185,
            "text": (
                "Field 39 contains a code that defines the response to a "
                "request. Code 00 indicates approval."
            ),
        }
        unrelated_hsm_section = {
            "source": "PUGD0537-004 Core Host Commands V1.pdf",
            "heading": "Appendix K - AS2805.3 PIN block formats",
            "page": 717,
            "text": (
                "payShield 10K Core Host Commands Appendix K AS2805 "
                "Format 1 PIN block is used in situations where the "
                "account number is not available."
            ),
        }
        wrong_ed_section = {
            "source": "PUGD0537-004 Core Host Commands V1.pdf",
            "heading": "EA Command / ED Response Message",
            "page": 55,
            "text": (
                "RESPONSE MESSAGE Response Code Value 'ED' Error Code "
                "'01': Key parity error, advice only"
            ),
        }
        command = {
            "command": "EC",
            "response_command": "ED",
            "hsm_result_code": "ED01",
            "return_code": "01",
            "request_message": "ECS000",
        }

        self.assertTrue(is_hsm_reference_section(hsm_section))
        self.assertFalse(is_hsm_reference_section(visa_section))
        self.assertEqual(
            extract_hsm_return_code_meaning(hsm_section, command),
            "PIN verification failure",
        )

        transaction = {
            "hsm_analysis": {
                "thread": "00112634",
                "commands": [command],
            }
        }

        findings = asyncio.run(
            build_hsm_documentation_findings(
                transaction=transaction,
                pdf_sections=[
                    visa_section,
                    unrelated_hsm_section,
                    wrong_ed_section,
                    hsm_section,
                ],
                query="Analyse HSM",
            )
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(
            command["return_code_meaning"],
            "PIN verification failure",
        )
        self.assertEqual(
            command["documented_return_code_line"],
            "'01': PIN verification failure",
        )
        self.assertNotIn(
            "AS2805 Format 1 PIN block",
            command["return_code_meaning"],
        )
        self.assertNotIn(
            "Key parity error",
            command["return_code_meaning"],
        )
        self.assertEqual(
            findings[0]["source"],
            "PUGD0537-004 Core Host Commands V1.pdf",
        )
        self.assertEqual(findings[0]["page"], 42)

    def test_hsm_result_code_meaning_uses_neighbor_command_context(self):
        wrong_ed_section = {
            "source": "PUGD0537-004 Core Host Commands V1.pdf",
            "heading": "EA Command / ED Response Message",
            "page": 55,
            "section_index": 1,
            "chunk_index": 0,
            "text": (
                "RESPONSE MESSAGE Response Code Value 'ED' Error Code "
                "'01': Key parity error, advice only"
            ),
        }
        ec_command_section = {
            "source": "PUGD0537-004 Core Host Commands V1.pdf",
            "heading": "EC Command",
            "page": 259,
            "section_index": 20,
            "chunk_index": 0,
            "text": "COMMAND MESSAGE Command Code Value 'EC'",
        }
        ec_response_section = {
            "source": "PUGD0537-004 Core Host Commands V1.pdf",
            "heading": "Response Message",
            "page": 260,
            "section_index": 21,
            "chunk_index": 0,
            "text": (
                "RESPONSE MESSAGE Response Code Value 'ED' Error Code "
                "'00': No error '01': PIN verification failure "
                "'10': ZPK parity error '27': PVK not double length"
            ),
        }
        command = {
            "command": "EC",
            "response_command": "ED",
            "hsm_result_code": "ED01",
            "return_code": "01",
            "request_message": "ECS000",
        }

        section, meaning = find_hsm_return_code_meaning_section(
            command=command,
            hsm_pdf_sections=[
                wrong_ed_section,
                ec_command_section,
                ec_response_section,
            ],
        )

        self.assertEqual(meaning, "PIN verification failure")
        self.assertEqual(section["page"], 260)
        self.assertNotIn("Key parity error", meaning)


if __name__ == "__main__":
    unittest.main()
