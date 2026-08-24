import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.conversation_memory import ConversationMemoryState  # noqa: E402
from app.services.conversation_memory_service import (  # noqa: E402
    ConversationContextResolver,
    state_from_resolution,
)
from app.services.generic_question_classifier_service import (  # noqa: E402
    GenericQuestionClassifier,
)


def state(**kwargs):
    return ConversationMemoryState(
        conversation_id="conv-1",
        active_entities=kwargs.pop("active_entities", {}),
        active_topic=kwargs.pop("active_topic", None),
        recent_entities=kwargs.pop("recent_entities", {}),
        last_intent=kwargs.pop("last_intent", None),
        **kwargs,
    )


class ConversationMemoryResolverTests(unittest.TestCase):
    def classify(self, query):
        return GenericQuestionClassifier.classify(query)

    def test_followup_code_51_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Et le 51 ?",
            state(active_entities={"field_number": "039"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["field_number"], "039")
        self.assertEqual(resolved.explicit_entities["code"], "51")
        self.assertEqual(
            resolved.resolved_query,
            "Que signifie Field 039 = 51 ?",
        )
        self.assertEqual(self.classify(resolved.resolved_query).intent, "VALUE_LOOKUP")

    def test_followup_code_55_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Et le 55 ?",
            state(active_entities={"field_number": "039"}),
        )

        self.assertEqual(resolved.inherited_entities["field_number"], "039")
        self.assertEqual(resolved.explicit_entities["code"], "55")
        self.assertEqual(self.classify(resolved.resolved_query).intent, "VALUE_LOOKUP")

    def test_explicit_field_overrides_memory(self):
        resolved = ConversationContextResolver.resolve(
            "Et pour Field 003 ?",
            state(active_entities={"field_number": "039", "code": "51"}),
        )

        self.assertNotIn("field_number", resolved.inherited_entities)
        self.assertEqual(resolved.explicit_entities["field_number"], "003")
        self.assertIn("Field 003", resolved.resolved_query)
        self.assertNotIn("039", resolved.resolved_query)

    def test_value_lookup_then_other_code(self):
        resolved = ConversationContextResolver.resolve(
            "Et le 55 ?",
            state(active_entities={"field_number": "039", "code": "51"}),
        )

        self.assertEqual(resolved.inherited_entities["field_number"], "039")
        self.assertEqual(resolved.explicit_entities["code"], "55")

    def test_all_codes_followup_becomes_table_lookup(self):
        resolved = ConversationContextResolver.resolve(
            "Donne-moi tous les codes.",
            state(active_entities={"field_number": "039"}),
        )

        self.assertEqual(
            resolved.resolved_query,
            "Quels sont tous les codes ou valeurs du Field 039 ?",
        )
        self.assertEqual(self.classify(resolved.resolved_query).intent, "TABLE_LOOKUP")

    def test_reject_codes_inherit_field(self):
        resolved = ConversationContextResolver.resolve(
            "Et les reject codes ?",
            state(active_entities={"field_number": "039"}),
        )

        self.assertEqual(
            resolved.resolved_query,
            "Quels sont les reject codes du Field 039 ?",
        )
        self.assertEqual(self.classify(resolved.resolved_query).intent, "RULE_LOOKUP")

    def test_location_followup_inherits_concept(self):
        resolved = ConversationContextResolver.resolve(
            "Où en parle-t-on ?",
            state(active_entities={"concept": "STIP"}, active_topic="STIP"),
        )

        self.assertEqual(
            resolved.resolved_query,
            "Où parle-t-on de STIP dans la documentation ?",
        )
        self.assertEqual(self.classify(resolved.resolved_query).intent, "LOCATION")

    def test_reversal_followup_changes_concept(self):
        resolved = ConversationContextResolver.resolve(
            "Et les reversals ?",
            state(active_entities={"concept": "message matching"}),
        )

        self.assertEqual(resolved.explicit_entities["concept"], "reversal")
        self.assertIn("reversals", resolved.resolved_query.lower())

    def test_ambiguous_field_history_does_not_invent_field(self):
        resolved = ConversationContextResolver.resolve(
            "Et le 51 ?",
            state(recent_entities={"field_numbers": ["039", "038", "003"]}),
        )

        self.assertTrue(resolved.ambiguity_detected)
        self.assertNotIn("field_number", resolved.inherited_entities)
        self.assertEqual(resolved.explicit_entities["code"], "51")

    def test_assistant_error_is_not_used_as_fact(self):
        resolved = ConversationContextResolver.resolve(
            "Et le 10 ?",
            state(active_entities={"field_number": "039", "code": "51"}),
        )

        self.assertEqual(resolved.resolved_query, "Que signifie Field 039 = 10 ?")
        self.assertNotIn("Acceptation", resolved.resolved_query)
        self.assertNotIn("Partial approval", resolved.resolved_query)

    def test_pronominal_structure_followup_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Quelle est sa structure ?",
            state(active_entities={"field_number": "002"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["field_number"], "002")
        self.assertEqual(resolved.resolved_query, "Quelle est la structure du Field 002 ?")

    def test_pronominal_mandatory_followup_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Est-il obligatoire ?",
            state(active_entities={"field_number": "002"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["field_number"], "002")
        self.assertEqual(resolved.resolved_query, "Le Field 002 est-il obligatoire ?")

    def test_pronominal_messages_followup_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Dans quels messages est-il utilisé ?",
            state(active_entities={"field_number": "002"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["field_number"], "002")
        self.assertEqual(
            resolved.resolved_query,
            "Dans quels messages le Field 002 est-il utilise ?",
        )

    def test_pronominal_format_followup_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Quel est son format ?",
            state(active_entities={"field_number": "002"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["field_number"], "002")
        self.assertEqual(resolved.resolved_query, "Quel est le format du Field 002 ?")

    def test_pronominal_length_followup_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Quelle est sa longueur ?",
            state(active_entities={"field_number": "002"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["field_number"], "002")
        self.assertEqual(resolved.resolved_query, "Quelle est la longueur du Field 002 ?")

    def test_pronominal_attributes_followup_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Quels sont ses attributs ?",
            state(active_entities={"field_number": "002"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["field_number"], "002")
        self.assertEqual(resolved.resolved_query, "Quels sont les attributs du Field 002 ?")

    def test_short_codes_followup_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Et les codes ?",
            state(active_entities={"field_number": "002"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["field_number"], "002")
        self.assertEqual(resolved.resolved_query, "Quels sont les codes ou valeurs du Field 002 ?")

    def test_missing_active_context_marks_pronominal_followup_ambiguous(self):
        resolved = ConversationContextResolver.resolve(
            "Est-il obligatoire ?",
            state(),
        )

        self.assertFalse(resolved.used_memory)
        self.assertTrue(resolved.ambiguity_detected)
        self.assertNotIn("field_number", resolved.inherited_entities)

    def test_message_type_followup_inherits_active_message(self):
        resolved = ConversationContextResolver.resolve(
            "Dans quels cas est-il utilisé ?",
            state(active_entities={"message_type": "0100"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["message_type"], "0100")
        self.assertEqual(
            resolved.resolved_query,
            "Dans quels cas le message 0100 est-il utilise ?",
        )

    def test_multiturn_field_context_switches_to_explicit_field(self):
        initial_state = state()
        first = ConversationContextResolver.resolve(
            "Que représente le Field 002 ?",
            initial_state,
        )
        after_first = state_from_resolution(
            state=initial_state,
            resolved=first,
            classification=self.classify(first.resolved_query),
        )
        second = ConversationContextResolver.resolve(
            "Et le Field 003 ?",
            after_first,
        )
        after_second = state_from_resolution(
            state=after_first,
            resolved=second,
            classification=self.classify(second.resolved_query),
        )
        third = ConversationContextResolver.resolve(
            "Quelle est sa structure ?",
            after_second,
        )

        self.assertEqual(after_first.active_entities["field_number"], "002")
        self.assertEqual(after_second.active_entities["field_number"], "003")
        self.assertEqual(third.inherited_entities["field_number"], "003")
        self.assertEqual(third.resolved_query, "Quelle est la structure du Field 003 ?")

    def test_state_tracks_active_previous_and_entity_history(self):
        initial_state = state()
        first = ConversationContextResolver.resolve(
            "Que represente le Field 002 ?",
            initial_state,
        )
        after_first = state_from_resolution(
            state=initial_state,
            resolved=first,
            classification=self.classify(first.resolved_query),
        )
        second = ConversationContextResolver.resolve(
            "Et le Field 003 ?",
            after_first,
        )
        after_second = state_from_resolution(
            state=after_first,
            resolved=second,
            classification=self.classify(second.resolved_query),
        )
        third = ConversationContextResolver.resolve(
            "Et le Field 039 ?",
            after_second,
        )
        after_third = state_from_resolution(
            state=after_second,
            resolved=third,
            classification=self.classify(third.resolved_query),
        )

        self.assertEqual(after_third.active_entity["value"], "039")
        self.assertEqual(after_third.previous_entity["value"], "003")
        self.assertEqual(
            [item["value"] for item in after_third.entity_history],
            ["002", "003", "039"],
        )

    def test_conversation_recall_previous_field_before_current_does_not_use_rag(self):
        memory_state = state(
            active_entities={"field_number": "039"},
            active_entity={"type": "field", "value": "039", "label": "Field 039"},
            previous_entity={"type": "field", "value": "003", "label": "Field 003"},
            entity_history=[
                {"type": "field", "value": "002", "label": "Field 002"},
                {"type": "field", "value": "003", "label": "Field 003"},
                {"type": "field", "value": "039", "label": "Field 039"},
            ],
        )
        resolved = ConversationContextResolver.resolve(
            "Quel Field analysions-nous juste avant le 039 ?",
            memory_state,
        )

        self.assertEqual(resolved.query_type, "CONVERSATION_RECALL")
        self.assertEqual(resolved.recall_answer, "Field 003.")
        self.assertEqual(resolved.inherited_entities["field_number"], "003")
        self.assertTrue(resolved.used_memory)

    def test_conversation_recall_ambiguous_without_history(self):
        resolved = ConversationContextResolver.resolve(
            "Quel etait le sujet precedent ?",
            state(),
        )

        self.assertEqual(resolved.query_type, "CONVERSATION_RECALL")
        self.assertTrue(resolved.ambiguity_detected)
        self.assertIsNone(resolved.recall_answer)

    def test_semantic_presence_followup_inherits_active_field(self):
        resolved = ConversationContextResolver.resolve(
            "Y a-t-il des situations où sa présence n'est pas nécessaire ?",
            state(active_entities={"field_number": "003"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(resolved.inherited_entities["field_number"], "003")
        self.assertIn("Field 003", resolved.resolved_query)
        self.assertIn("presence", resolved.resolved_query)

    def test_semantic_absence_followup_inherits_active_field(self):
        for question in (
            "Peut-on ne pas le renseigner ?",
            "Que se passe-t-il si on l'omet ?",
            "Sa présence est-elle toujours requise ?",
        ):
            with self.subTest(question=question):
                resolved = ConversationContextResolver.resolve(
                    question,
                    state(active_entities={"field_number": "003"}),
                )

                self.assertTrue(resolved.used_memory)
                self.assertEqual(resolved.inherited_entities["field_number"], "003")
                self.assertIn("Field 003", resolved.resolved_query)

    def test_semantic_same_followup_inherits_active_field_when_context_is_clear(self):
        resolved = ConversationContextResolver.resolve(
            "Est-ce pareil pour lui ?",
            state(active_entities={"field_number": "003"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertFalse(resolved.ambiguity_detected)
        self.assertEqual(resolved.inherited_entities["field_number"], "003")
        self.assertEqual(resolved.resolved_query, "Est-ce pareil pour le Field 003 ?")

    def test_semantic_same_followup_is_ambiguous_without_context(self):
        resolved = ConversationContextResolver.resolve(
            "Est-ce pareil pour lui ?",
            state(),
        )

        self.assertTrue(resolved.ambiguity_detected)
        self.assertFalse(resolved.used_memory)

    def test_recall_cursor_supports_multiple_before_questions_without_changing_active(self):
        memory_state = state(
            active_entities={"field_number": "039"},
            active_entity={"type": "field", "value": "039", "label": "Field 039"},
            previous_entity={"type": "field", "value": "003", "label": "Field 003"},
            entity_history=[
                {"type": "field", "value": "002", "label": "Field 002"},
                {"type": "field", "value": "003", "label": "Field 003"},
                {"type": "field", "value": "039", "label": "Field 039"},
            ],
        )

        first_recall = ConversationContextResolver.resolve(
            "Quel Field analysions-nous avant ?",
            memory_state,
        )
        after_first_recall = state_from_resolution(
            state=memory_state,
            resolved=first_recall,
            classification=self.classify(first_recall.resolved_query),
        )
        second_recall = ConversationContextResolver.resolve(
            "Et avant ?",
            after_first_recall,
        )
        after_second_recall = state_from_resolution(
            state=after_first_recall,
            resolved=second_recall,
            classification=self.classify(second_recall.resolved_query),
        )
        normal_followup = ConversationContextResolver.resolve(
            "Quel est son format ?",
            after_second_recall,
        )

        self.assertEqual(first_recall.query_type, "CONVERSATION_RECALL")
        self.assertEqual(first_recall.recall_answer, "Field 003.")
        self.assertEqual(after_first_recall.active_entity["value"], "039")
        self.assertEqual(second_recall.recall_answer, "Field 002.")
        self.assertEqual(after_second_recall.active_entity["value"], "039")
        self.assertEqual(normal_followup.inherited_entities["field_number"], "039")
        self.assertEqual(normal_followup.resolved_query, "Quel est le format du Field 039 ?")

    def test_recall_history_list_order_and_active_field(self):
        memory_state = state(
            active_entities={"field_number": "039"},
            active_entity={"type": "field", "value": "039", "label": "Field 039"},
            previous_entity={"type": "field", "value": "003", "label": "Field 003"},
            entity_history=[
                {"type": "field", "value": "002", "label": "Field 002"},
                {"type": "field", "value": "003", "label": "Field 003"},
                {"type": "field", "value": "039", "label": "Field 039"},
            ],
        )

        fields = ConversationContextResolver.resolve(
            "Quels Fields avons-nous analysés ?",
            memory_state,
        )
        order = ConversationContextResolver.resolve(
            "Dans quel ordre ?",
            memory_state,
        )
        active = ConversationContextResolver.resolve(
            "Quel est le Field actuellement actif ?",
            memory_state,
        )

        self.assertEqual(fields.query_type, "CONVERSATION_RECALL")
        self.assertEqual(fields.recall_answer, "Field 002 -> Field 003 -> Field 039.")
        self.assertEqual(order.recall_answer, "Field 002 -> Field 003 -> Field 039.")
        self.assertEqual(active.recall_answer, "Le Field actuellement actif est le Field 039.")

    def test_explicit_entity_switch_resets_recall_cursor_and_keeps_previous(self):
        memory_state = state(
            active_entities={"field_number": "003"},
            active_entity={"type": "field", "value": "003", "label": "Field 003"},
            previous_entity={"type": "field", "value": "002", "label": "Field 002"},
            entity_history=[
                {"type": "field", "value": "002", "label": "Field 002"},
                {"type": "field", "value": "003", "label": "Field 003"},
            ],
            recall_cursor=0,
        )
        switch = ConversationContextResolver.resolve(
            "Passons au Field 039.",
            memory_state,
        )
        after_switch = state_from_resolution(
            state=memory_state,
            resolved=switch,
            classification=self.classify(switch.resolved_query),
        )
        recall = ConversationContextResolver.resolve(
            "Quel était le précédent ?",
            after_switch,
        )
        after_recall = state_from_resolution(
            state=after_switch,
            resolved=recall,
            classification=self.classify(recall.resolved_query),
        )
        followup = ConversationContextResolver.resolve(
            "Et son format ?",
            after_recall,
        )

        self.assertEqual(after_switch.active_entity["value"], "039")
        self.assertEqual(after_switch.previous_entity["value"], "003")
        self.assertIsNone(after_switch.recall_cursor)
        self.assertEqual(recall.recall_answer, "Field 003.")
        self.assertEqual(followup.inherited_entities["field_number"], "039")

    def test_active_field_recall_does_not_trigger_document_query(self):
        memory_state = state(
            active_entities={"field_number": "003"},
            active_entity={"type": "field", "value": "003", "label": "Field 003"},
            previous_entity={"type": "field", "value": "002", "label": "Field 002"},
            entity_history=[
                {"type": "field", "value": "002", "label": "Field 002"},
                {"type": "field", "value": "003", "label": "Field 003"},
            ],
        )

        resolved = ConversationContextResolver.resolve(
            "Quel est le Field actuellement actif ?",
            memory_state,
        )
        after_recall = state_from_resolution(
            state=memory_state,
            resolved=resolved,
            classification=self.classify(resolved.resolved_query),
        )

        self.assertEqual(resolved.query_type, "CONVERSATION_RECALL")
        self.assertEqual(resolved.recall_answer, "Le Field actuellement actif est le Field 003.")
        self.assertEqual(after_recall.active_entity["value"], "003")

    def test_previous_recall_does_not_destroy_active_field(self):
        memory_state = state(
            active_entities={"field_number": "003"},
            active_entity={"type": "field", "value": "003", "label": "Field 003"},
            previous_entity={"type": "field", "value": "002", "label": "Field 002"},
            entity_history=[
                {"type": "field", "value": "002", "label": "Field 002"},
                {"type": "field", "value": "003", "label": "Field 003"},
            ],
        )

        recall = ConversationContextResolver.resolve(
            "Quel était le Field précédent ?",
            memory_state,
        )
        after_recall = state_from_resolution(
            state=memory_state,
            resolved=recall,
            classification=self.classify(recall.resolved_query),
        )
        followup = ConversationContextResolver.resolve(
            "Quel est son format ?",
            after_recall,
        )

        self.assertEqual(recall.query_type, "CONVERSATION_RECALL")
        self.assertEqual(recall.recall_answer, "Field 002.")
        self.assertEqual(after_recall.active_entity["value"], "003")
        self.assertEqual(followup.inherited_entities["field_number"], "003")

    def test_active_object_tracks_codes_without_replacing_active_field(self):
        memory_state = state()
        first = ConversationContextResolver.resolve(
            "Que représente le Field 039 ?",
            memory_state,
        )
        after_first = state_from_resolution(
            state=memory_state,
            resolved=first,
            classification=self.classify(first.resolved_query),
        )
        codes = ConversationContextResolver.resolve(
            "Quels sont tous ses codes ?",
            after_first,
        )
        after_codes = state_from_resolution(
            state=after_first,
            resolved=codes,
            classification=self.classify(codes.resolved_query),
        )
        these_codes = ConversationContextResolver.resolve(
            "Donnez-moi ces codes et leurs significations.",
            after_codes,
        )
        after_these_codes = state_from_resolution(
            state=after_codes,
            resolved=these_codes,
            classification=self.classify(these_codes.resolved_query),
        )
        format_followup = ConversationContextResolver.resolve(
            "Quel est son format ?",
            after_these_codes,
        )

        self.assertEqual(codes.inherited_entities["field_number"], "039")
        self.assertEqual(codes.resolved_query, "Quels sont tous les codes ou valeurs du Field 039 ?")
        self.assertEqual(after_codes.active_object["content_kind"], "codes")
        self.assertEqual(after_codes.active_object["entity_value"], "039")
        self.assertEqual(these_codes.inherited_entities["field_number"], "039")
        self.assertEqual(
            these_codes.resolved_query,
            "Donne-moi les codes du Field 039 et leurs significations.",
        )
        self.assertEqual(after_these_codes.active_entity["value"], "039")
        self.assertEqual(format_followup.inherited_entities["field_number"], "039")

    def test_object_reference_without_active_object_is_ambiguous(self):
        resolved = ConversationContextResolver.resolve(
            "Donnez-moi ces valeurs.",
            state(active_entities={"field_number": "039"}),
        )

        self.assertTrue(resolved.ambiguity_detected)
        self.assertEqual(resolved.ambiguity_reason, "CONVERSATION_OBJECT_CONTEXT_AMBIGUOUS")
        self.assertNotIn("field_number", resolved.inherited_entities)

    def test_explicit_function_becomes_active_entity(self):
        initial_state = state()
        resolved = ConversationContextResolver.resolve(
            "Depuis le fichier Excel, explique-moi la fonction GetOriginalAuthData.",
            initial_state,
        )
        after = state_from_resolution(
            state=initial_state,
            resolved=resolved,
            classification=self.classify(resolved.resolved_query),
        )

        self.assertEqual(resolved.explicit_entities["function_name"], "GetOriginalAuthData")
        self.assertEqual(after.active_entities["function_name"], "GetOriginalAuthData")
        self.assertEqual(after.active_entity["type"], "function")
        self.assertEqual(after.active_entity["value"], "GetOriginalAuthData")

    def test_function_exceptions_followup_inherits_active_function(self):
        resolved = ConversationContextResolver.resolve(
            "Quelles sont les exceptions de cette fonction ?",
            state(active_entities={"function_name": "GetOriginalAuthData"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(
            resolved.inherited_entities["function_name"],
            "GetOriginalAuthData",
        )
        self.assertEqual(
            resolved.resolved_query,
            "Quelles sont les exceptions de la fonction GetOriginalAuthData ?",
        )

    def test_function_example_followup_without_pronoun_inherits_active_function(self):
        resolved = ConversationContextResolver.resolve(
            "Donne un exemple concret",
            state(active_entities={"function_name": "GetOriginalAuthData"}),
        )

        self.assertTrue(resolved.used_memory)
        self.assertEqual(
            resolved.inherited_entities["function_name"],
            "GetOriginalAuthData",
        )
        self.assertEqual(
            resolved.resolved_query,
            "Donne un exemple concret pour la fonction GetOriginalAuthData.",
        )

    def test_explicit_function_overrides_active_function(self):
        resolved = ConversationContextResolver.resolve(
            "Quelles sont les exceptions de la fonction CheckLimits ?",
            state(active_entities={"function_name": "GetOriginalAuthData"}),
        )

        self.assertEqual(resolved.explicit_entities["function_name"], "CheckLimits")
        self.assertNotIn("function_name", resolved.inherited_entities)
        self.assertIn("CheckLimits", resolved.resolved_query)
        self.assertNotIn("GetOriginalAuthData", resolved.resolved_query)

    def test_function_followup_keeps_previous_requested_exception_topic(self):
        initial_state = state()
        first = ConversationContextResolver.resolve(
            "Quelles sont les exceptions de la fonction CheckLimits ?",
            initial_state,
        )
        after_first = state_from_resolution(
            state=initial_state,
            resolved=first,
            classification=self.classify(first.resolved_query),
        )
        second = ConversationContextResolver.resolve(
            "et pour CardInSaf ?",
            after_first,
        )

        self.assertEqual(after_first.active_object["entity_type"], "function")
        self.assertEqual(after_first.active_object["content_kind"], "exceptions")
        self.assertEqual(second.explicit_entities["function_name"], "CardInSaf")
        self.assertEqual(second.inherited_entities["function_topic"], "exceptions")
        self.assertEqual(
            second.resolved_query,
            "Quelles sont les exceptions de la fonction CardInSaf ?",
        )


if __name__ == "__main__":
    unittest.main()
