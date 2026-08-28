import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.document_content import ContentUnit, EvidenceBundle
from app.services.field_value_decoder_service import (
    decode_field_value_from_evidence,
    decode_field_value_from_sources,
)


def field_022_source_text() -> str:
    return "\n".join(
        [
            "4.16 Field 22-Point-of-Service Entry Mode Code",
            "Positions 1-2: PAN and Date Entry Mode",
            "00 | Unknown or terminal not used | Use when account number and expiration date capture method not known.",
            "01 | Manual key entry | Indicates card data not obtained from chip or magnetic stripe.",
            "10 | Credential on file | Merchant initiates transaction for cardholder using credentials stored on file.",
            "Position 3: PIN Entry Capability",
            "0 | Unknown | Indicates PIN capability of terminal cannot be determined.",
            "1 | Terminal can accept PINs | Indicates terminal can accept and forward online PINs.",
            "2 | Terminal cannot accept PINs | Indicates terminal cannot accept and forward online PINs.",
            "Position 4: Fill",
            "0 | Unused | Not used for Visa and Visa Electron.",
        ]
    )


class FieldValueDecoderServiceTests(unittest.TestCase):
    def test_decodes_composite_field_value_from_documented_positions(self):
        decoding = decode_field_value_from_sources(
            field_number="022",
            value="1000",
            sources=[
                {
                    "source_id": "S1",
                    "text": field_022_source_text(),
                }
            ],
        )

        self.assertIsNotNone(decoding)
        self.assertTrue(decoding.complete)
        self.assertEqual([row.value for row in decoding.rows], ["10", "0", "0"])
        self.assertEqual(decoding.rows[0].meaning, "Credential on file")
        self.assertEqual(decoding.rows[1].meaning, "Unknown")
        self.assertEqual(decoding.rows[2].meaning, "Unused")

    def test_short_observed_value_is_marked_partial_not_invented(self):
        decoding = decode_field_value_from_sources(
            field_number="022",
            value="100",
            sources=[
                {
                    "source_id": "S1",
                    "text": field_022_source_text(),
                }
            ],
        )

        self.assertIsNotNone(decoding)
        self.assertFalse(decoding.complete)
        self.assertEqual([row.value for row in decoding.rows], ["10", "0", "Non present"])
        self.assertIn("Position 4 absente", decoding.issues[0])

    def test_does_not_decode_without_documented_position_markers(self):
        decoding = decode_field_value_from_sources(
            field_number="022",
            value="1000",
            sources=[
                {
                    "source_id": "S1",
                    "text": "Code Definition\n10 Credential on file\n0 Unknown",
                }
            ],
        )

        self.assertIsNone(decoding)

    def test_decodes_from_evidence_bundle_units(self):
        unit = ContentUnit(
            unit_id="U1",
            document_id="D1",
            content_type="table",
            entities={"field_number": "022"},
            primary_entities={"field_number": "022"},
            content="Table 4-7 Field 22 POS Entry Mode Codes",
            raw_text=field_022_source_text(),
        )
        bundle = EvidenceBundle(
            query="Que signifie Field 022 = 1000 ?",
            intent="VALUE_LOOKUP",
            entities={"field_number": "022", "code": "1000"},
            retrieved_units=[unit],
        )

        decoding = decode_field_value_from_evidence(
            bundle=bundle,
            field_number="022",
            value="1000",
        )

        self.assertIsNotNone(decoding)
        self.assertEqual(decoding.rows[0].component, "PAN and Date Entry Mode")
        self.assertEqual(decoding.source_ids, ["U1"])


if __name__ == "__main__":
    unittest.main()
