import unittest

from app.services.generic_question_classifier_service import (
    GenericQuestionClassifier,
    QueryPlanner,
)


class GenericQuestionClassifierTests(unittest.TestCase):
    def assert_intent(
        self,
        question: str,
        expected_intent: str,
    ) -> None:
        classification = GenericQuestionClassifier.classify(question)

        self.assertEqual(classification.intent, expected_intent)

    def test_field_lookup(self):
        classification = GenericQuestionClassifier.classify(
            "Que représente Field 039 ?"
        )

        self.assertEqual(classification.intent, "FIELD_LOOKUP")
        self.assertEqual(classification.entities.field_numbers, ["039"])

    def test_value_lookup(self):
        classification = GenericQuestionClassifier.classify(
            "Que signifie 039=51 ?"
        )

        self.assertEqual(classification.intent, "VALUE_LOOKUP")
        self.assertEqual(classification.entities.field_numbers, ["039"])
        self.assertEqual(classification.entities.codes, ["51"])

    def test_table_lookup(self):
        classification = GenericQuestionClassifier.classify(
            "Donne-moi tous les codes du Field 039"
        )
        plan = QueryPlanner.build(classification)

        self.assertEqual(classification.intent, "TABLE_LOOKUP")
        self.assertTrue(plan.require_complete_table)
        self.assertIn("structured_table_lookup", plan.strategies)

    def test_definition(self):
        classification = GenericQuestionClassifier.classify(
            "Qu'est-ce que STIP ?"
        )

        self.assertEqual(classification.intent, "DEFINITION")
        self.assertIn("STIP", classification.entities.concepts)

    def test_explanation(self):
        self.assert_intent(
            "Explique le message matching.",
            "EXPLANATION",
        )

    def test_procedure(self):
        self.assert_intent(
            "Comment fonctionne un reversal ?",
            "PROCEDURE",
        )

    def test_summary(self):
        classification = GenericQuestionClassifier.classify(
            "Résume le chapitre 2."
        )
        plan = QueryPlanner.build(classification)

        self.assertEqual(classification.intent, "SUMMARY")
        self.assertEqual(classification.entities.chapter, "2")
        self.assertTrue(plan.require_hierarchy)

    def test_comparison(self):
        classification = GenericQuestionClassifier.classify(
            "Quelle différence entre 0100 et 0200 ?"
        )
        plan = QueryPlanner.build(classification)

        self.assertEqual(classification.intent, "COMPARISON")
        self.assertEqual(classification.entities.message_types, ["0100", "0200"])
        self.assertTrue(plan.require_multiple_targets)
        self.assertEqual(
            plan.comparison_targets,
            [{"message_type": "0100"}, {"message_type": "0200"}],
        )

    def test_location(self):
        classification = GenericQuestionClassifier.classify(
            "Où parle-t-on de STIP ?"
        )
        plan = QueryPlanner.build(classification)

        self.assertEqual(classification.intent, "LOCATION")
        self.assertIn("lexical_exact_lookup", plan.strategies)

    def test_rule_lookup(self):
        classification = GenericQuestionClassifier.classify(
            "Quels sont les reject codes du Field 039 ?"
        )

        self.assertEqual(classification.intent, "RULE_LOOKUP")
        self.assertEqual(classification.entities.field_numbers, ["039"])


if __name__ == "__main__":
    unittest.main()
