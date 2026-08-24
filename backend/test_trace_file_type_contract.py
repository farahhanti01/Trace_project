import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.extraction_service import extract_document
from app.services.file_type_service import (
    is_image_extension,
    is_supported_document_extension,
    is_trace_extension,
    with_trace_extension_query,
)


class TraceFileTypeContractTests(unittest.TestCase):
    def test_trc_numbered_extension_is_supported_as_trace(self):
        self.assertTrue(is_trace_extension(".TRC068"))
        self.assertTrue(is_supported_document_extension(".TRC068"))
        self.assertTrue(is_trace_extension(".trc019"))

    def test_trace_extension_query_includes_trc_regex(self):
        query = with_trace_extension_query({
            "conversation_id": "conversation",
            "agent": "log",
        })

        self.assertEqual(query["conversation_id"], "conversation")
        self.assertEqual(query["agent"], "log")
        self.assertIn("$or", query)
        self.assertTrue(
            any(
                condition.get("extension", {}).get("$regex") == r"^\.trc\d+$"
                for condition in query["$or"]
            )
        )

    def test_extract_document_reads_trc068_as_text(self):
        temp_dir = BACKEND_ROOT / ".tmp_tests"
        temp_dir.mkdir(exist_ok=True)
        trace_path = temp_dir / "TRACE_SAMPLE.TRC068"

        try:
            trace_path.write_text(
                "2014 00000001 6| Start DumpVisa()\n"
                "2014 00000002 6| - M.T.I : [0200]\n",
                encoding="utf-8",
            )

            extracted = extract_document(trace_path)
        finally:
            if trace_path.exists():
                trace_path.unlink()
            try:
                temp_dir.rmdir()
            except OSError:
                pass

        self.assertIn("Start DumpVisa", extracted["text"])
        self.assertGreaterEqual(extracted["line_count"], 2)

    def test_image_extensions_are_supported_documents(self):
        self.assertTrue(is_image_extension(".PNG"))
        self.assertTrue(is_supported_document_extension(".jpg"))
        self.assertTrue(is_supported_document_extension(".webp"))

    def test_extract_document_accepts_image_without_required_ocr(self):
        temp_dir = BACKEND_ROOT / ".tmp_tests"
        temp_dir.mkdir(exist_ok=True)
        image_path = temp_dir / "screenshot.png"

        try:
            image_path.write_bytes(
                bytes.fromhex(
                    "89504E470D0A1A0A0000000D494844520000000100000001"
                    "08060000001F15C4890000000A49444154789C6360000002"
                    "0001E221BC330000000049454E44AE426082"
                )
            )

            extracted = extract_document(image_path)
        finally:
            if image_path.exists():
                image_path.unlink()
            try:
                temp_dir.rmdir()
            except OSError:
                pass

        self.assertIn("Capture d'ecran importee", extracted["text"])
        self.assertGreaterEqual(len(extracted["sections"]), 1)
        self.assertTrue(str(extracted["encoding"]).startswith("image:"))

    def test_image_ocr_sections_detect_visible_trace_elements(self):
        from app.services.extraction_service import build_image_sections

        sections = build_image_sections(
            file_path=Path("trace-screen.png"),
            ocr_text=(
                "- M.T.I : [0110]\n"
                "- FLD (039) : (002) : [51]\n"
                "HsmResultCode ED01\n"
                "End GetService (0)"
            ),
            encoding="image:ocr",
        )

        headings = [section.get("heading") for section in sections]
        field_sections = [
            section
            for section in sections
            if section.get("field_number") == "039"
        ]

        self.assertIn("Capture d'ecran OCR > MTI", headings)
        self.assertIn("Capture d'ecran OCR > HSM", headings)
        self.assertIn(
            "Capture d'ecran OCR > Fonctions et erreurs",
            headings,
        )
        self.assertEqual(len(field_sections), 1)
        self.assertIn("Field 039", field_sections[0]["text"])


if __name__ == "__main__":
    unittest.main()
