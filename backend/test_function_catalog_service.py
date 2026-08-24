import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.function_catalog_service import (  # noqa: E402
    FunctionCatalogExtractor,
    extract_status_blocks,
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


if __name__ == "__main__":
    unittest.main()
