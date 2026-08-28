import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.function_catalog_service import (  # noqa: E402
    FunctionCatalogExtractor,
    answer_function_question,
    extract_error_cases,
    extract_status_blocks,
    function_overview,
    merge_function_entries,
    requested_function_topic,
)


class FunctionCatalogServiceTests(unittest.TestCase):
    def test_extracts_function_and_error_cases_from_structured_row(self):
        sections = [
            {
                "text": (
                    "GetOriginalAuthData | Appelle le package PL/SQL pour "
                    "retrouver l'autorisation originale | Exception: "
                    "NO_DATA_FOUND si aucune autorisation n'est trouvee | "
                    "result_flag = -1"
                ),
                "sheet": "Functions",
                "paragraph": 12,
                "row_number": 12,
                "heading": "Functions",
                "cells": [
                    {"column": 1, "value": "GetOriginalAuthData"},
                    {"column": 2, "value": "Exception: NO_DATA_FOUND"},
                ],
                "section_index": 0,
                "chunk_index": 0,
            }
        ]
        entries = FunctionCatalogExtractor.extract(
            sections=sections,
            document={
                "_id": "doc-1",
                "conversation_id": "conv-1",
                "original_filename": "Spec PowerCARD.xlsx",
            },
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["function_name"], "GetOriginalAuthData")
        self.assertEqual(entries[0]["normalized_function_name"], "getoriginalauthdata")
        self.assertTrue(entries[0]["exceptions"])
        self.assertEqual(entries[0]["sheet"], "Functions")
        self.assertEqual(entries[0]["row_number"], 12)

    def test_does_not_create_function_from_uppercase_constant(self):
        entries = FunctionCatalogExtractor.extract(
            sections=[
                {
                    "text": "ERROR_CODE | NO_DATA_FOUND | status NOK",
                    "sheet": "Functions",
                    "paragraph": 2,
                }
            ],
            document={"_id": "doc-1", "original_filename": "Spec.xlsx"},
        )

        self.assertEqual(entries, [])

    def test_merge_keeps_exceptions_for_target_function_only(self):
        entries = FunctionCatalogExtractor.extract(
            sections=[
                {
                    "text": "GetOriginalAuthData | Exception: NO_DATA_FOUND | result = -1",
                    "sheet": "Functions",
                    "paragraph": 1,
                },
                {
                    "text": "CheckLimits | Error: LIMIT_EXCEEDED | result = -2",
                    "sheet": "Functions",
                    "paragraph": 2,
                },
            ],
            document={"_id": "doc-1", "original_filename": "Spec.xlsx"},
        )
        target_entries = [
            entry
            for entry in entries
            if entry["function_name"] == "GetOriginalAuthData"
        ]
        merged = merge_function_entries(target_entries)

        self.assertTrue(any("NO_DATA_FOUND" in item for item in merged["exceptions"]))
        self.assertFalse(any("LIMIT_EXCEEDED" in item for item in merged["exceptions"]))

    def test_requested_topic_detects_exceptions_and_examples(self):
        self.assertEqual(
            requested_function_topic("quelles sont les exceptions de cette fonction"),
            "exceptions",
        )
        self.assertEqual(
            requested_function_topic("Donne un exemple concret"),
            "example",
        )

    def test_extracts_complete_return_code_blocks(self):
        text = """CheckLimits | NOK (-1) :
- TLV mal formate
- Echec de recuperation du tag SECURITY_VERIF_RESULT du TLV.
- Les controles de velocite ont echoue :
  * Echec lors de l'extraction de tags TLV obligatoires.
  * Error to get BANK_CODE from BANK

OK (0) :
- Tous les controles ont ete effectues avec succes.

ERROR (-2) :
- Exception WHEN OTHERS dans le code PL/SQL.
- Echec lors de l'extraction de tag TLV SECURITY_VERIF_RESULT."""
        blocks = extract_status_blocks(text)

        self.assertEqual([block["title"] for block in blocks], ["NOK (-1)", "OK (0)", "ERROR (-2)"])
        self.assertEqual(len(blocks[0]["details"]), 5)
        self.assertIn("TLV mal formate", blocks[0]["details"][0])
        self.assertFalse(blocks[1]["is_exception"])
        self.assertTrue(blocks[2]["is_exception"])

    def test_extracts_status_blocks_from_flattened_spreadsheet_row_without_codes(self):
        text = (
            "AuthRequestProc | Sinon NOK -> AuthLocal() -> fin | "
            "NOK : | Autorisation locale indisponible | Transaction deja traitee | "
            "OK : | Autorisation traitee avec succes"
        )

        blocks = extract_status_blocks(text)

        self.assertEqual([block["title"] for block in blocks], ["NOK", "OK"])
        self.assertTrue(blocks[0]["is_exception"])
        self.assertFalse(blocks[1]["is_exception"])
        self.assertIn("Autorisation locale indisponible", blocks[0]["details"])
        self.assertIn("Transaction deja traitee", blocks[0]["details"])
        self.assertNotIn("Autorisation traitee avec succes", blocks[0]["details"])

    def test_error_cases_skip_bare_status_headers(self):
        cases = extract_error_cases("AuthRequestProc | NOK : | Error to call AuthLocal")

        self.assertFalse(any(case.strip().upper() == "NOK :" for case in cases))
        self.assertTrue(any("Error to call AuthLocal" in case for case in cases))

    def test_function_overview_separates_role_from_metadata_and_statuses(self):
        entry = {
            "description": (
                "AuthRequestProc | libtrn | src/libs/libtrn/auth_request_proc.c | "
                "La fonction AuthRequestProc() traite une demande d'autorisation. "
                "Elle decide de traiter localement, envoyer vers un systeme externe "
                "ou refuser la transaction. | NOK : | Controle KO | OK : | Suite normale"
            ),
            "cells": [
                {"column": 1, "value": "AuthRequestProc"},
                {"column": 2, "value": "libtrn"},
                {"column": 3, "value": "src/libs/libtrn/auth_request_proc.c"},
                {
                    "column": 4,
                    "value": (
                        "La fonction AuthRequestProc() traite une demande "
                        "d'autorisation."
                    ),
                },
                {"column": 5, "value": "NOK :"},
                {"column": 6, "value": "Controle KO"},
            ],
        }

        overview = function_overview(entry, "AuthRequestProc")

        self.assertEqual(overview["metadata"]["library"], "libtrn")
        self.assertEqual(
            overview["metadata"]["source_path"],
            "src/libs/libtrn/auth_request_proc.c",
        )
        self.assertIn("traite une demande", overview["description"])
        self.assertNotIn("libtrn", overview["description"])
        self.assertNotIn("NOK", overview["description"])

    def test_inline_system_malfunction_status_is_extracted(self):
        blocks = extract_status_blocks(
            "CardInSaf | SYSTEM_MALFUNCTION (-2): Exception PL/SQL WHEN OTHERS"
        )

        self.assertEqual(blocks[0]["title"], "SYSTEM_MALFUNCTION (-2)")
        self.assertIn("Exception PL/SQL", blocks[0]["details"][0])

    def test_getservice_spreadsheet_row_keeps_ok_nok_blocks(self):
        entries = FunctionCatalogExtractor.extract(
            sections=[
                {
                    "text": (
                        "GetService | libactions | "
                        "La fonction GetService fait appel au package : "
                        "PCRD_P7_SERVICES.GET_SERVICE | "
                        "NOK (-1) : | - TLV DATA mal formate | "
                        "- Error in GET_CENTER_BANK | "
                        "- NO DATA FOUND in P7_SERVICES_CRITERIA Table | "
                        "OK (0) : | - Une ligne est trouvee dans "
                        "P7_SERVICES_CRITERIA avec discrimination_flag = N | "
                        "- GET_SERVICES_DEFINITION retourne la definition du service"
                    ),
                    "sheet": "Lib",
                    "paragraph": 3,
                    "row_number": 3,
                    "heading": "Lib",
                    "section_index": 0,
                    "chunk_index": 0,
                }
            ],
            document={
                "_id": "doc-1",
                "conversation_id": "conv-1",
                "original_filename": "Spec PowerCARD.xlsx",
            },
        )

        self.assertEqual(len(entries), 1)
        blocks = entries[0]["status_blocks"]
        self.assertEqual([block["title"] for block in blocks], ["NOK (-1)", "OK (0)"])
        self.assertIn("TLV DATA mal formate", blocks[0]["details"][0])
        self.assertIn("GET_CENTER_BANK", " ".join(blocks[0]["details"]))
        self.assertIn("GET_SERVICES_DEFINITION", " ".join(blocks[1]["details"]))


class FunctionCatalogAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_function_overview_answer_includes_documented_status_blocks(self):
        entries = FunctionCatalogExtractor.extract(
            sections=[
                {
                    "text": (
                        "GetService | libactions | "
                        "La fonction GetService fait appel au package : "
                        "PCRD_P7_SERVICES.GET_SERVICE | "
                        "NOK (-1) : | - TLV DATA mal formate | "
                        "- Error in GET_CENTER_BANK | "
                        "OK (0) : | - Une ligne est trouvee dans "
                        "P7_SERVICES_CRITERIA avec discrimination_flag = N"
                    ),
                    "sheet": "Lib",
                    "paragraph": 3,
                    "row_number": 3,
                    "heading": "Lib",
                    "section_index": 0,
                    "chunk_index": 0,
                }
            ],
            document={
                "_id": "doc-1",
                "conversation_id": "conv-1",
                "original_filename": "Spec PowerCARD.xlsx",
            },
        )

        with patch(
            "app.services.function_catalog_service.find_function_catalog_entries",
            return_value=entries,
        ):
            response = await answer_function_question(
                question=(
                    "Depuis Spec PowerCARD.xlsx, donne une description "
                    "detaillee de la fonction GetService"
                ),
                conversation_id="conv-1",
            )

        self.assertIsNotNone(response)
        sections = response["sections"]
        titles = [section["title"] for section in sections]
        self.assertIn("Role", titles)
        self.assertIn("Codes retour / comportements documentes", titles)

        status_section = next(
            section
            for section in sections
            if section["title"] == "Codes retour / comportements documentes"
        )
        labels = [item["label"] for item in status_section["items"]]
        details = "\n".join(item["content"] for item in status_section["items"])
        self.assertEqual(labels, ["NOK (-1)", "OK (0)"])
        self.assertIn("TLV DATA mal formate", details)
        self.assertIn("GET_CENTER_BANK", details)
        self.assertIn("P7_SERVICES_CRITERIA", details)


if __name__ == "__main__":
    unittest.main()
