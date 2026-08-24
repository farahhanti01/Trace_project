import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.entity_consistency_service import (  # noqa: E402
    check_evidence_completeness,
    filter_units_for_entity_consistency,
)
from app.services.generic_question_classifier_service import (  # noqa: E402
    GenericQuestionClassifier,
    QueryPlanner,
)


class EntityConsistencyFilterTests(unittest.TestCase):
    def test_filters_mismatched_primary_field_for_field_lookup(self):
        classification = GenericQuestionClassifier.classify(
            "Que represente le Field 003 ?"
        )
        plan = QueryPlanner.build(classification)
        units = [
            {
                "unit_id": "u1",
                "content_type": "field_description",
                "entities": {"field_number": "003"},
                "primary_entities": {"field_number": "003"},
                "content": "Field 003 description",
            },
            {
                "unit_id": "u2",
                "content_type": "field_description",
                "entities": {"field_number": "004"},
                "primary_entities": {"field_number": "004"},
                "content": "Field 004 description",
            },
        ]

        filtered, diagnostics = filter_units_for_entity_consistency(units, plan)

        self.assertEqual([unit["unit_id"] for unit in filtered], ["u1"])
        self.assertEqual(filtered[0]["entity_alignment"], "primary_match")
        self.assertEqual(diagnostics["removed_mismatches"], 1)

    def test_rule_lookup_without_rule_evidence_is_incomplete(self):
        classification = GenericQuestionClassifier.classify(
            "Le Field 003 est-il obligatoire ?"
        )
        plan = QueryPlanner.build(classification)

        missing = check_evidence_completeness(
            plan=plan,
            units=[
                {
                    "unit_id": "u1",
                    "content_type": "paragraph",
                    "entities": {"field_number": "003"},
                    "content": "General paragraph",
                }
            ],
        )

        self.assertTrue(missing)


if __name__ == "__main__":
    unittest.main()
