import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.screenshot_analysis_service import (
    DOCUMENTATION_LOOKUP,
    SCREEN_DIAGNOSIS,
    SCREEN_EXTRACTION,
    SCREEN_UNDERSTANDING,
    classify_screenshot_intent,
    extraction_response_from_visible_facts,
    image_content_parts,
    intent_requires_documentation,
    normalize_screenshot_payload,
    parse_json_object,
    should_load_conversation_images,
)


class ScreenshotAnalysisServiceTests(unittest.TestCase):
    def test_screenshot_intent_classifies_extraction_without_documentation_lookup(self):
        self.assertEqual(
            classify_screenshot_intent("depuis le screen, extrait le champ 037"),
            SCREEN_EXTRACTION,
        )
        self.assertEqual(
            classify_screenshot_intent("donne-moi le MTI"),
            SCREEN_EXTRACTION,
        )
        self.assertEqual(
            classify_screenshot_intent("et le champ 002 ?"),
            SCREEN_EXTRACTION,
        )
        self.assertEqual(
            classify_screenshot_intent("que signifie le Field 039 ?"),
            DOCUMENTATION_LOOKUP,
        )
        self.assertEqual(
            classify_screenshot_intent("le PIN est bien passé ?"),
            SCREEN_DIAGNOSIS,
        )
        self.assertEqual(
            classify_screenshot_intent("que représente ce screen ?"),
            SCREEN_UNDERSTANDING,
        )

    def test_screenshot_followups_can_reuse_latest_conversation_image(self):
        self.assertTrue(should_load_conversation_images(SCREEN_EXTRACTION))
        self.assertTrue(should_load_conversation_images(SCREEN_DIAGNOSIS))
        self.assertFalse(should_load_conversation_images(DOCUMENTATION_LOOKUP))

    def test_extraction_response_uses_observed_iso_field_only(self):
        response = extraction_response_from_visible_facts(
            question="extrait le champ 037",
            visible_facts={
                "fields": [
                    {
                        "field_number": "037",
                        "namespace": "ISO8583_FIELD",
                        "value": "601318105410",
                        "line": "FLD (037) : (012) : [601318105410]",
                    },
                    {
                        "field_number": "002",
                        "namespace": "HSM_COMMAND_PARAMETER",
                        "value": "01",
                    },
                ]
            },
        )

        self.assertEqual(response["summary"], "Field 037 : 601318105410")
        self.assertIn("Ligne detectee", response["sections"][0]["content"])
        self.assertEqual(response["references"][0]["source"], "Screenshot")

    def test_extraction_response_does_not_replace_missing_field_with_documentation(self):
        response = extraction_response_from_visible_facts(
            question="extrait le champ 039",
            visible_facts={
                "fields": [
                    {
                        "field_number": "037",
                        "namespace": "ISO8583_FIELD",
                        "value": "601318105410",
                    }
                ]
            },
        )

        self.assertIn("Field 039 n'est pas visible", response["summary"])
        self.assertEqual(response["references"][0]["source"], "Screenshot")

    def test_extraction_does_not_trigger_documentation_but_hsm_diagnosis_can(self):
        self.assertFalse(
            intent_requires_documentation(
                intent=SCREEN_EXTRACTION,
                question="extrait le champ 039",
                visible_facts={"fields": []},
            )
        )
        self.assertTrue(
            intent_requires_documentation(
                intent=SCREEN_DIAGNOSIS,
                question="le PIN est bien passé ?",
                visible_facts={"hsm": {"hsm_result_code": "ED01"}},
            )
        )

    def test_normalize_screenshot_payload_keeps_only_used_references(self):
        normalized = normalize_screenshot_payload(
            payload={
                "summary": "ED01 est documenté.",
                "sections": [
                    {
                        "title": "Références",
                        "content": "La documentation explique ED01.",
                        "source_ids": ["S2"],
                    }
                ],
            },
            references=[
                {"source_id": "S1", "source": "Unused.pdf"},
                {"source_id": "S2", "source": "Used.pdf"},
            ],
        )

        self.assertEqual(len(normalized["references"]), 1)
        self.assertEqual(normalized["references"][0]["source_id"], "S2")

    def test_image_content_parts_send_real_multimodal_image_url(self):
        temp_dir = BACKEND_ROOT / ".tmp_tests"
        temp_dir.mkdir(exist_ok=True)
        image_path = temp_dir / "screen.png"

        try:
            image_path.write_bytes(
                bytes.fromhex(
                    "89504E470D0A1A0A0000000D494844520000000100000001"
                    "08060000001F15C4890000000A49444154789C6360000002"
                    "0001E221BC330000000049454E44AE426082"
                )
            )
            parts = image_content_parts([
                {
                    "relative_path": ".tmp_tests/screen.png",
                    "extension": ".png",
                }
            ])
        finally:
            if image_path.exists():
                image_path.unlink()
            try:
                temp_dir.rmdir()
            except OSError:
                pass

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["type"], "image_url")
        self.assertEqual(parts[0]["image_url"]["detail"], "high")
        self.assertTrue(
            parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
        )

    def test_normalize_screenshot_payload_keeps_structured_response_shape(self):
        normalized = normalize_screenshot_payload(
            payload={
                "summary": "La capture montre un echange HSM ED01.",
                "sections": [
                    {
                        "title": "Analyse HSM",
                        "content": "FROM HSM contient ED01.",
                        "source_ids": ["S1"],
                    }
                ],
                "issues": [],
                "recommendations": ["Verifier la documentation HSM."],
            },
            references=[
                {
                    "source_id": "S1",
                    "source": "PUGD0537-004 Core Host Commands V1.pdf",
                    "pdf_page": 276,
                }
            ],
        )

        self.assertEqual(
            normalized["summary"],
            "La capture montre un echange HSM ED01.",
        )
        self.assertEqual(normalized["sections"][0]["title"], "Analyse HSM")
        self.assertEqual(normalized["sections"][0]["source_ids"], ["S1"])
        self.assertEqual(normalized["story"], [])
        self.assertEqual(normalized["references"][0]["source_id"], "S1")

    def test_parse_json_object_recovers_json_wrapped_in_text(self):
        parsed = parse_json_object(
            'Voici le JSON: {"summary": "OK", "sections": []}'
        )

        self.assertEqual(parsed["summary"], "OK")
        self.assertEqual(parsed["sections"], [])


if __name__ == "__main__":
    unittest.main()
