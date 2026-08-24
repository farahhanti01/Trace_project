import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.document_service import (
    duplicate_document_query,
    sha256_hex,
)


class DocumentIngestionContractTests(unittest.TestCase):
    def test_sha256_hex_is_stable_for_same_content(self):
        content = b"PUGD0537-004 Core Host Commands"

        self.assertEqual(
            sha256_hex(content),
            sha256_hex(content),
        )
        self.assertNotEqual(
            sha256_hex(content),
            sha256_hex(b"another document"),
        )

    def test_duplicate_document_query_scopes_hash_to_conversation_and_agent(self):
        query = duplicate_document_query(
            conversation_id="conversation-1",
            agent="log",
            extension=".pdf",
            file_hash="abc123",
        )

        self.assertEqual(query["conversation_id"], "conversation-1")
        self.assertEqual(query["agent"], "log")
        self.assertEqual(query["extension"], ".pdf")
        self.assertEqual(query["file_hash"], "abc123")
        self.assertIn("extracted", query["status"]["$in"])


if __name__ == "__main__":
    unittest.main()
