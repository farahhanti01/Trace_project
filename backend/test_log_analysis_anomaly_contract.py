import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.log_analysis_agent_service import (
    build_pdf_documentation_findings,
    observed_pdf_anomalies,
)


class LogAnalysisAnomalyContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
