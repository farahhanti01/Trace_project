import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.extraction_service import extract_document
from app.services.file_type_service import (
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


if __name__ == "__main__":
    unittest.main()
