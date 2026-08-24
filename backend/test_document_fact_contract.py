import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.document_fact_service import (
    extract_document_facts,
    extract_hsm_return_code_facts_from_section,
)
from app.services.log_analysis_agent_service import (
    hsm_finding_from_structured_fact,
)


class DocumentFactContractTests(unittest.TestCase):
    def test_hsm_return_codes_are_extracted_from_response_table(self):
        document = {
            "_id": "doc-1",
            "conversation_id": "conversation-1",
            "agent": "log",
            "extension": ".pdf",
            "original_filename": "PUGD0537-004 Core Host Commands V1.pdf",
        }
        section = {
            "document_id": "doc-1",
            "heading": "EC Command / ED Response Message",
            "page": 260,
            "section_index": 10,
            "chunk_index": 0,
            "text": (
                "RESPONSE MESSAGE Response Code Value 'ED' Error Code "
                "'00': No error '01': PIN verification failure "
                "'10': ZPK parity error '27': PVK not double length "
                "or a standard error code."
            ),
        }

        facts = extract_hsm_return_code_facts_from_section(
            section=section,
            document=document,
        )

        by_result_code = {
            fact["hsm_result_code"]: fact
            for fact in facts
        }

        self.assertEqual(by_result_code["ED01"]["meaning"], "PIN verification failure")
        self.assertEqual(by_result_code["ED27"]["meaning"], "PVK not double length")
        self.assertEqual(by_result_code["ED27"]["command"], "EC")
        self.assertEqual(by_result_code["ED27"]["response_command"], "ED")
        self.assertEqual(by_result_code["ED27"]["page"], 260)

    def test_document_facts_are_deduplicated(self):
        document = {
            "_id": "doc-1",
            "original_filename": "PUGD0537-004 Core Host Commands V1.pdf",
        }
        section = {
            "document_id": "doc-1",
            "heading": "EC Command / ED Response Message",
            "page": 260,
            "text": (
                "RESPONSE MESSAGE Response Code Value 'ED' Error Code "
                "'01': PIN verification failure '01': PIN verification failure"
            ),
        }

        facts = extract_document_facts(
            sections=[section],
            document=document,
        )

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["hsm_result_code"], "ED01")

    def test_hsm_return_code_inherits_command_from_previous_section(self):
        document = {
            "_id": "doc-1",
            "conversation_id": "conversation-1",
            "agent": "log",
            "extension": ".pdf",
            "original_filename": "PUGD0537-004 Core Host Commands V1.pdf",
        }
        sections = [
            {
                "document_id": "doc-1",
                "heading": "EC Command",
                "page": 259,
                "section_index": 20,
                "chunk_index": 0,
                "text": "COMMAND MESSAGE Command Code Value 'EC'",
            },
            {
                "document_id": "doc-1",
                "heading": "Response Message",
                "page": 260,
                "section_index": 21,
                "chunk_index": 0,
                "text": (
                    "RESPONSE MESSAGE Response Code Value 'ED' Error Code "
                    "'00': No error '01': PIN verification failure "
                    "'27': PVK not double length"
                ),
            },
        ]

        facts = extract_document_facts(
            sections=sections,
            document=document,
        )

        by_result_code = {
            fact["hsm_result_code"]: fact
            for fact in facts
        }

        self.assertEqual(by_result_code["ED01"]["command"], "EC")
        self.assertEqual(
            by_result_code["ED01"]["meaning"],
            "PIN verification failure",
        )
        self.assertEqual(
            by_result_code["ED01"]["command_context"],
            "previous_section",
        )

    def test_hsm_finding_uses_structured_fact_meaning(self):
        command = {
            "command": "EC",
            "response_command": "ED",
            "hsm_result_code": "ED27",
            "return_code": "27",
            "thread": "00112634",
        }
        fact = {
            "source": "PUGD0537-004 Core Host Commands V1.pdf",
            "heading": "EC Command / ED Response Message",
            "page": 260,
            "meaning": "PVK not double length",
        }

        finding = hsm_finding_from_structured_fact(
            command=command,
            transaction_thread="00112634",
            fact=fact,
        )

        self.assertEqual(finding["return_code_meaning"], "PVK not double length")
        self.assertEqual(
            finding["documented_return_code_line"],
            "'27': PVK not double length",
        )
        self.assertIn("ED27", finding["explanation"])
        self.assertEqual(finding["source"], "PUGD0537-004 Core Host Commands V1.pdf")


if __name__ == "__main__":
    unittest.main()
