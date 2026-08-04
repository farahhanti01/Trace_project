import sys
import unittest
import json
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_ROOT.parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.main import StructuredResponse
from app.services.chat_intent_router import classify_chat_workflow
from app.services.documentation_agent_service import normalize_agent_response
from app.services.documentation_synthesis_service import (
    ResponsePlanner,
    dedupe_code_rows,
    enforce_value_table_when_requested,
    extract_code_rows_from_text,
    extract_explicit_topics,
    filter_knowledge_for_plan,
    infer_field_nature_from_knowledge,
    parse_json_object,
    validate_response_against_plan,
    validate_table_block,
    validate_final_documentation_answer,
)


class DocumentationAgentContractTests(unittest.TestCase):
    def test_structured_response_keeps_sections_and_references(self):
        response = StructuredResponse(
            summary="Resume",
            sections=[
                {
                    "title": "Role",
                    "content": "Contenu",
                    "paragraphs": ["Premier paragraphe.", "Deuxieme paragraphe."],
                    "items": [
                        {
                            "label": "Positions 1-2",
                            "content": "Type de transaction.",
                        }
                    ],
                    "blocks": [
                        {
                            "type": "paragraph",
                            "content": "Le champ est structure.",
                        },
                        {
                            "type": "table",
                            "columns": [
                                {"key": "positions", "label": "Positions"},
                                {"key": "meaning", "label": "Signification"},
                            ],
                            "rows": [
                                {
                                    "positions": "1-2",
                                    "meaning": "Type de transaction",
                                }
                            ],
                        },
                    ],
                    "source_ids": ["S1"],
                }
            ],
            references=[
                {
                    "source": "BASE I Technical Specifications, Volume 1",
                    "pdf_page": 114,
                    "printed_page": "4-10",
                    "section": "Field 3-Processing Code",
                }
            ],
        )

        dumped = response.model_dump()

        self.assertEqual(dumped["sections"][0]["title"], "Role")
        self.assertEqual(dumped["sections"][0]["paragraphs"][0], "Premier paragraphe.")
        self.assertEqual(dumped["sections"][0]["items"][0]["label"], "Positions 1-2")
        self.assertEqual(dumped["sections"][0]["blocks"][1]["type"], "table")
        self.assertNotIn("pages_used", dumped)
        self.assertEqual(dumped["references"][0]["pdf_page"], 114)

    def test_sections_disable_story_fallback(self):
        selected_sections = [
            {
                "source": "vip-system-BASE-i-tech-specs-volume-1.pdf",
                "source_id": "S1",
                "page": 114,
                "page_document": "4-10",
                "heading": "Field 3-Processing Code",
                "text": "Field 003 identifies the transaction type.",
            }
        ]
        payload = {
            "summary": "Le Field 003 identifie le type de transaction.",
            "sections": [
                {
                    "title": "Role",
                    "paragraphs": [
                        "Le Field 003 identifie le type de transaction et les comptes concernes.",
                        "Il aide le systeme a choisir le traitement metier applicable.",
                    ],
                    "items": [
                        {
                            "label": "Positions 1-2",
                            "content": "Type de transaction.",
                        }
                    ],
                    "blocks": [
                        {
                            "type": "paragraph",
                            "content": "Le Field 003 identifie le type de transaction.",
                        }
                    ],
                    "source_ids": ["S1"],
                }
            ],
            "story": ["ancien extrait brut qui ne doit pas etre affiche"],
            "references": [{"source_id": "S1"}],
        }

        response = normalize_agent_response(
            payload=payload,
            selected_sections=selected_sections,
            enforce_story_sources=True,
            question="Explique le role du Field 003.",
        )

        self.assertEqual(response["story"], [])
        self.assertEqual(response["sections"][0]["title"], "Role")
        self.assertEqual(len(response["sections"][0]["paragraphs"]), 2)
        self.assertEqual(response["sections"][0]["items"][0]["label"], "Positions 1-2")
        self.assertEqual(response["sections"][0]["blocks"][0]["type"], "paragraph")
        self.assertNotIn("pages_used", response)
        self.assertEqual(response["references"][0]["pdf_page"], 114)

    def test_nested_json_summary_is_unwrapped_before_display(self):
        selected_sections = [
            {
                "source": "vip-system-BASE-i-tech-specs-volume-1.pdf",
                "source_id": "S1",
                "page": 185,
                "page_document": "4-81",
                "heading": "Field 39-Response Code",
                "text": "Field 39 contains a response code.",
            }
        ]
        nested = {
            "summary": "Le Field 039 contient le code de reponse.",
            "sections": [
                {
                    "title": "Valeurs et significations",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "content": "Les codes indiquent le resultat de la demande.",
                        },
                        {
                            "type": "table",
                            "content": {
                                "header": ["Code", "Signification"],
                                "rows": [
                                    ["00", "Approbation"],
                                    ["05", "Refus"],
                                ],
                            },
                        },
                    ],
                    "source_ids": ["S1"],
                }
            ],
            "references": [{"source_id": "S1"}],
        }

        response = normalize_agent_response(
            payload={
                "summary": json.dumps(nested),
                "story": ["ancien detail brut"],
            },
            selected_sections=selected_sections,
            enforce_story_sources=True,
            question="Field 039 codes tableau",
        )

        self.assertEqual(response["summary"], "Le Field 039 contient le code de reponse.")
        self.assertEqual(response["story"], [])
        self.assertEqual(response["sections"][0]["title"], "Valeurs et significations")
        self.assertEqual(response["sections"][0]["blocks"][1]["type"], "table")
        self.assertEqual(response["sections"][0]["blocks"][1]["columns"][0]["label"], "Code")
        self.assertEqual(response["sections"][0]["blocks"][1]["rows"][0]["code"], "00")

    def test_parse_json_object_unwraps_quoted_json_payload(self):
        nested = {
            "summary": "Resume propre.",
            "sections": [
                {
                    "title": "Role",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "content": "Contenu propre.",
                        }
                    ],
                    "source_ids": ["S1"],
                }
            ],
        }
        payload = parse_json_object(json.dumps(json.dumps(nested)))

        self.assertEqual(payload["summary"], "Resume propre.")
        self.assertEqual(payload["sections"][0]["title"], "Role")

    def test_raw_pdf_fragment_is_rejected(self):
        errors = validate_final_documentation_answer(
            payload={
                "summary": "Resume court.",
                "sections": [
                    {
                        "title": "Role",
                        "content": "Field 3 Chapter 4 Data Field Descriptions...",
                        "source_ids": ["S1"],
                    }
                ],
                "references": [{"source_id": "S1"}],
            },
            intent="ISO_FIELD_EXPLANATION",
            user_language="fr",
            available_source_ids={"S1"},
        )

        self.assertTrue(any("raw fragment" in error for error in errors))

    def test_unknown_source_id_is_rejected(self):
        errors = validate_final_documentation_answer(
            payload={
                "summary": "Resume court.",
                "sections": [
                    {
                        "title": "Role",
                        "content": "Le champ identifie le type de transaction.",
                        "source_ids": ["S9"],
                    }
                ],
                "references": [{"source_id": "S9"}],
            },
            intent="ISO_FIELD_EXPLANATION",
            user_language="fr",
            available_source_ids={"S1"},
        )

        self.assertTrue(any("unknown source_id" in error for error in errors))

    def test_summary_has_maximum_four_sentences(self):
        errors = validate_final_documentation_answer(
            payload={
                "summary": "Un. Deux. Trois. Quatre. Cinq.",
                "sections": [
                    {
                        "title": "Role",
                        "content": "Le champ identifie le type de transaction.",
                        "source_ids": ["S1"],
                    }
                ],
            },
            intent="ISO_FIELD_EXPLANATION",
            user_language="fr",
            available_source_ids={"S1"},
        )

        self.assertTrue(any("four sentences" in error for error in errors))

    def test_french_question_rejects_mostly_english_section(self):
        errors = validate_final_documentation_answer(
            payload={
                "summary": "Resume court.",
                "sections": [
                    {
                        "title": "Role",
                        "content": (
                            "The field contains the transaction account code "
                            "and is used in the authorization request response."
                        ),
                        "source_ids": ["S1"],
                    }
                ],
            },
            intent="ISO_FIELD_EXPLANATION",
            user_language="fr",
            available_source_ids={"S1"},
        )

        self.assertTrue(any("mostly English" in error for error in errors))

    def test_short_technical_section_is_rejected(self):
        errors = validate_final_documentation_answer(
            payload={
                "summary": "Resume court.",
                "sections": [
                    {
                        "title": "Rôle",
                        "paragraphs": ["Le champ sert au traitement."],
                        "source_ids": ["S1"],
                    }
                ],
            },
            intent="ISO_FIELD_EXPLANATION",
            user_language="fr",
            available_source_ids={"S1"},
        )

        self.assertTrue(any("too short" in error for error in errors))

    def test_valid_table_block_is_accepted(self):
        errors = validate_table_block(
            {
                "type": "table",
                "columns": [
                    {"key": "positions", "label": "Positions"},
                    {"key": "meaning", "label": "Signification"},
                ],
                "rows": [
                    {
                        "positions": "1-2",
                        "meaning": "Type de transaction",
                    }
                ],
            },
            user_language="fr",
        )

        self.assertEqual(errors, [])

    def test_invalid_table_block_is_rejected(self):
        errors = validate_table_block(
            {
                "type": "table",
                "columns": [{"key": "positions", "label": "Positions"}],
                "rows": [{"positions": ""}],
            },
            user_language="fr",
        )

        self.assertTrue(any("at least two columns" in error for error in errors))

    def test_frontend_details_view_is_legacy_only(self):
        component = (
            PROJECT_ROOT
            / "frontend"
            / "src"
            / "components"
            / "AIResponseCard.jsx"
        ).read_text(encoding="utf-8")

        self.assertIn("hasDocumentationSections", component)
        self.assertIn("!hasDocumentationSections && data.story", component)
        self.assertIn("function ResponseBlock", component)
        self.assertIn("function ResponseTable", component)
        self.assertNotIn("formatPageUsed", component)

    def test_role_question_plan_is_dynamic(self):
        question = "Explique le rôle du Field 003 dans une transaction d’autorisation."
        knowledge = {
            "role": "Le Field 003 identifie la nature de l'operation.",
            "format": "Champ numerique fixe de six chiffres.",
            "structure": [
                {
                    "category": "structure",
                    "fact": "Positions 1-2 type de transaction, 3-4 compte source, 5-6 compte destination.",
                    "source_ids": ["S1"],
                }
            ],
            "validation": [
                {
                    "category": "validation",
                    "fact": "Reject code 0008 si valeur invalide.",
                    "source_ids": ["S1"],
                }
            ],
            "limitations": [
                {
                    "category": "limitation",
                    "fact": "American Express utilise un cas particulier.",
                    "source_ids": ["S1"],
                }
            ],
            "facts": [],
        }
        plan = ResponsePlanner.build(
            intent="ISO_FIELD_EXPLANATION",
            knowledge=knowledge,
            question=question,
        )
        titles = [section["title"] for section in plan]

        self.assertEqual(
            titles,
            ["Résumé", "Rôle dans l’autorisation", "Structure du champ"],
        )
        self.assertEqual(
            extract_explicit_topics(question),
            {"role", "authorization_context", "structure"},
        )

    def test_filter_knowledge_removes_unrequested_special_cases(self):
        question = "Explique le rôle du Field 003 dans une transaction d’autorisation."
        plan = [
            {"title": "Résumé"},
            {"title": "Rôle dans l’autorisation"},
            {"title": "Structure du champ"},
        ]
        knowledge = {
            "summary": "Resume",
            "role": "Le Field 003 identifie l'operation.",
            "format": "Champ numerique fixe de six chiffres.",
            "structure": [
                {
                    "category": "structure",
                    "fact": "Positions 1-2, 3-4 et 5-6.",
                    "source_ids": ["S1"],
                }
            ],
            "validation": [
                {
                    "category": "validation",
                    "fact": "Reject code 0008.",
                    "source_ids": ["S1"],
                }
            ],
            "facts": [
                {
                    "category": "limitation",
                    "fact": "American Express et Field 152 sont des cas particuliers.",
                    "source_ids": ["S1"],
                }
            ],
            "source_ids": ["S1"],
        }
        filtered = filter_knowledge_for_plan(
            knowledge=knowledge,
            plan=plan,
            question=question,
        )

        self.assertIn("role", filtered)
        self.assertIn("format", filtered)
        self.assertIn("structure", filtered)
        self.assertNotIn("validation", filtered)
        self.assertEqual(filtered.get("facts"), [])

    def test_response_against_plan_rejects_unplanned_sections_and_pages_used(self):
        plan = [
            {"title": "Résumé"},
            {"title": "Rôle dans l’autorisation"},
            {"title": "Structure du champ"},
        ]
        errors = validate_response_against_plan(
            response={
                "summary": "Resume.",
                "pages_used": [],
                "sections": [
                    {
                        "title": "Contrôles",
                        "blocks": [
                            {
                                "type": "paragraph",
                                "content": "Reject code 0008.",
                            }
                        ],
                        "source_ids": ["S1"],
                    }
                ],
            },
            plan=plan,
            question="Explique le rôle du Field 003 dans une transaction d’autorisation.",
            available_source_ids={"S1"},
        )

        self.assertTrue(any("pages_used" in error for error in errors))
        self.assertTrue(any("section not planned" in error for error in errors))

    def test_response_rejects_promised_table_without_table_block(self):
        plan = [
            {"title": "RÃ©sumÃ©"},
            {"title": "Valeurs et significations"},
        ]
        errors = validate_response_against_plan(
            response={
                "summary": "Resume.",
                "sections": [
                    {
                        "title": "Valeurs et significations",
                        "blocks": [
                            {
                                "type": "paragraph",
                                "content": "Voici un tableau presentant les codes de reponse.",
                            }
                        ],
                        "source_ids": ["S1"],
                    }
                ],
            },
            plan=plan,
            question="Explique les codes possibles du Field 039.",
            available_source_ids={"S1"},
        )

        self.assertTrue(any("promises a table" in error for error in errors))

    def test_codes_question_uses_values_table_plan(self):
        plan = ResponsePlanner.build(
            intent="ISO_FIELD_EXPLANATION",
            knowledge={
                "field_nature": "code_list",
                "role": "Le champ contient des codes de reponse.",
                "facts": [
                    {
                        "category": "value",
                        "fact": "00 indique une approbation.",
                        "source_ids": ["S1"],
                    },
                    {
                        "category": "value",
                        "fact": "05 indique un refus.",
                        "source_ids": ["S1"],
                    },
                ],
            },
            question="Explique les codes possibles du Field 039.",
        )
        values_section = next(
            section
            for section in plan
            if section["title"] == "Valeurs et significations"
        )

        self.assertIn("table", values_section["preferred_blocks"])

    def test_field_nature_detects_code_list_for_response_code(self):
        nature = infer_field_nature_from_knowledge({
            "role": "Field 39 is a Response Code defining approval or decline.",
            "facts": [
                {
                    "category": "value",
                    "fact": "00 means approval and 05 means refusal.",
                    "source_ids": ["S1"],
                }
            ],
        })

        self.assertEqual(nature, "code_list")

    def test_field_nature_detects_composite_for_positioned_field(self):
        nature = infer_field_nature_from_knowledge({
            "format": "Champ numerique fixe de six chiffres.",
            "structure": [
                {
                    "category": "structure",
                    "fact": "Positions 1-2 indiquent le type de transaction, positions 3-4 le compte source et positions 5-6 le compte destination.",
                    "source_ids": ["S1"],
                }
            ],
        })

        self.assertEqual(nature, "composite")

    def test_composite_value_plan_adds_decoding_example_when_documented(self):
        plan = ResponsePlanner.build(
            intent="ISO_VALUE_DECODING",
            knowledge={
                "field_nature": "composite",
                "role": "Le champ identifie la nature de l'operation.",
                "structure": [
                    {
                        "category": "structure",
                        "fact": "Positions 1-2, 3-4 et 5-6 ont des sens distincts.",
                        "source_ids": ["S1"],
                    }
                ],
                "examples": [
                    {
                        "category": "example",
                        "fact": "000000 se decode en 00 / 00 / 00.",
                        "source_ids": ["S1"],
                    }
                ],
            },
            question="Que signifie Field 003 = 000000 ?",
        )
        titles = [section["title"] for section in plan]

        self.assertTrue(any("codage" in title.lower() for title in titles))
        self.assertTrue(any("exemple" in title.lower() for title in titles))

    def test_table_question_gets_real_table_block_from_sources(self):
        question = (
            "Pour le Field 039, presente les codes de reponse sous forme "
            "de tableau avec leur signification et les references."
        )
        plan = ResponsePlanner.build(
            intent="ISO_FIELD_EXPLANATION",
            knowledge={
                "field_nature": "code_list",
                "facts": [
                    {
                        "category": "value",
                        "fact": "Response code 00 means approval.",
                        "source_ids": ["S1"],
                    }
                ],
            },
            question=question,
        )
        payload = {
            "summary": "Le Field 039 contient des codes de reponse.",
            "sections": [
                {
                    "title": "Valeurs et significations",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "content": "Le tableau ci-dessous presente les codes.",
                        }
                    ],
                    "source_ids": ["S1"],
                }
            ],
        }
        response = enforce_value_table_when_requested(
            payload=payload,
            knowledge={
                "facts": [
                    {
                        "category": "value",
                        "fact": "Response code 00 means approval.",
                        "source_ids": ["S1"],
                    },
                    {
                        "category": "value",
                        "fact": "Response code 05 means decline.",
                        "source_ids": ["S2"],
                    },
                ]
            },
            plan=plan,
            selected_sections=[],
            question=question,
        )
        blocks = response["sections"][0]["blocks"]

        self.assertEqual(blocks[1]["type"], "table")
        self.assertIn(
            "00",
            {row["code"] for row in blocks[1]["rows"]},
        )
        self.assertIn(
            "05",
            {row["code"] for row in blocks[1]["rows"]},
        )

    def test_field_039_table_extracts_codes_from_pdf_table_text(self):
        question = (
            "Pour le Field 039, presente les codes de reponse sous forme "
            "de tableau avec leur signification et les references."
        )
        table_text = """
        Table 4-18 Field 39 Response Codes
        0110 msgs 0410 msgs 031x msgs 0x20
        Code Definition Issr STIP Issr STIP Inq Upd Adv
        00 Successful approval/completion or V.I.P. PIN verification is successful 1 1
        10 Partial approval
        51 Insufficient funds
        542 Expired card
        85 No reason to decline request for account number verification, address verification, CVV2 verification, or credit voucher
        """

        rows = dedupe_code_rows(
            extract_code_rows_from_text(table_text, ["S1"], question)
        )
        rows_by_code = {row["code"]: row for row in rows}

        for code in ("00", "10", "51", "54", "85"):
            self.assertIn(code, rows_by_code)

        self.assertEqual(rows_by_code["54"]["meaning"], "Expired card")
        self.assertNotIn("Table 4-18", rows_by_code["00"]["meaning"])

    def test_chat_router_keeps_documentation_questions_out_of_log_compliance(self):
        self.assertEqual(
            classify_chat_workflow(
                question="Pour le Field 039, presente les codes de reponse sous forme de tableau.",
                selected_agent="log",
            ),
            "DOCUMENTATION_QA",
        )

    def test_chat_router_sends_trace_analysis_to_log_compliance(self):
        self.assertEqual(
            classify_chat_workflow(
                question="Analyse cette trace.",
                selected_agent="log",
            ),
            "LOG_COMPLIANCE_ANALYSIS",
        )


if __name__ == "__main__":
    unittest.main()
