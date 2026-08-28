import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from app.database import (
    conversation_memory_states_collection,
    messages_collection,
)
from app.models.conversation_memory import (
    ConversationMemoryMessage,
    ConversationMemoryState,
    ResolvedConversationQuery,
)
from app.services.generic_question_classifier_service import (
    ClassificationResult,
    GenericQuestionClassifier,
    extract_generic_entities,
    normalize_field_number,
    normalize_for_search,
)

# conversation_id
# active_topic
# active_entity
logger = logging.getLogger(__name__)

MAX_RECENT_MESSAGES = int(os.getenv("DOCUMENTATION_MEMORY_RECENT_MESSAGES", "10"))
MAX_MEMORY_CHARACTERS = int(os.getenv("DOCUMENTATION_MEMORY_CHARACTERS", "6000"))
SUMMARY_MESSAGE_THRESHOLD = int(os.getenv("DOCUMENTATION_MEMORY_SUMMARY_AFTER", "24"))


def conversation_memory_enabled() -> bool:
    return os.getenv("DOCUMENTATION_CONVERSATION_MEMORY", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def attachment_document_ids(message: dict[str, Any]) -> list[str]:
    ids: list[str] = []

    for attachment in message.get("attachments") or []:
        document_id = attachment.get("document_id") or attachment.get("id")

        if document_id:
            ids.append(str(document_id))

    return unique(ids)


def first_value(values: list[str]) -> str | None:
    return values[0] if values else None


FUNCTION_TOKEN_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
FUNCTION_EXPLICIT_PATTERN = re.compile(
    r"\b(?:fonction|function)\s+([A-Za-z_][A-Za-z0-9_]{2,})(?:\s*\(\))?",
    flags=re.I,
)
FUNCTION_FOLLOWUP_NAME_PATTERN = re.compile(
    r"\b(?:et\s+pour|pour)\s+([A-Za-z_][A-Za-z0-9_]{2,})(?:\s*\(\))?",
    flags=re.I,
)
COMMON_NON_FUNCTION_TOKENS = {
    "field",
    "function",
    "fonction",
    "document",
    "chapter",
    "section",
    "table",
    "source",
    "status",
    "result",
    "return",
    "value",
    "error",
    "description",
    "exception",
    "exemple",
    "example",
}


def extract_requested_code_tokens(question: str, field_numbers: list[str] | None = None) -> list[str]:
    normalized = normalize_for_search(question)

    if not re.search(
        r"\b(codes?|valeurs?|values?|significations?|meanings?)\b",
        normalized,
    ):
        return []

    field_aliases = set(field_numbers or [])
    field_aliases.update(
        value.lstrip("0") or value
        for value in field_numbers or []
    )
    ignored_tokens = {
        "AN",
        "PDF",
        "MTI",
        "ISO",
        "HSM",
    }
    codes = []

    for token in re.findall(r"\b[A-Za-z0-9]{2,4}\b", question):
        code = token.upper()

        if code in ignored_tokens or code in field_aliases:
            continue

        if not re.search(r"\d", code):
            continue

        codes.append(code)

    return unique(codes)


def looks_like_function_name(value: str) -> bool:
    name = str(value or "").strip().strip("()")

    if len(name) < 3:
        return False

    normalized = re.sub(r"[^a-z0-9_]+", "", name.lower())

    if not normalized or normalized in COMMON_NON_FUNCTION_TOKENS:
        return False

    if name.isupper():
        return False

    return bool(
        re.match(r"^[A-Za-z_]", name)
        and re.search(r"[A-Za-z]", name)
        and (re.search(r"[a-z][A-Z]", name) or "_" in name)
    )


def extract_function_name(question: str) -> str | None:
    explicit_match = FUNCTION_EXPLICIT_PATTERN.search(question or "")

    if explicit_match and looks_like_function_name(explicit_match.group(1)):
        return explicit_match.group(1)

    followup_match = FUNCTION_FOLLOWUP_NAME_PATTERN.search(question or "")

    if followup_match and looks_like_function_name(followup_match.group(1)):
        return followup_match.group(1)

    if not re.search(r"\b(fonction|function)\b", question or "", flags=re.I):
        return None

    for token in FUNCTION_TOKEN_PATTERN.findall(question or ""):
        if looks_like_function_name(token):
            return token

    return None


def extract_current_entities(question: str) -> dict[str, Any]:
    """Extract explicit entities from the current turn only."""

    entities = extract_generic_entities(question)
    normalized = normalize_for_search(question)
    explicit: dict[str, Any] = {}

    if entities.field_numbers:
        explicit["field_number"] = entities.field_numbers[0]

    if entities.codes:
        explicit["codes"] = entities.codes
        explicit["code"] = entities.codes[0]

    if entities.message_types:
        explicit["message_type"] = entities.message_types[0]

    if entities.chapter:
        explicit["chapter"] = entities.chapter

    if entities.section:
        explicit["section"] = entities.section

    if entities.concepts:
        explicit["concept"] = entities.concepts[0]

    if "field_number" not in explicit:
        bare_field = re.search(
            r"\b(?:pour|field|fld|champ)\s+0*(\d{3}(?:\.\d+)?)\b",
            normalized,
        )

        if bare_field:
            explicit["field_number"] = normalize_field_number(bare_field.group(1))

    if "code" not in explicit:
        bare_code = re.search(
            r"\b(?:et\s+)?(?:le|la|code|valeur)\s+([A-Z]{0,2}\d{2,4})\b",
            question,
            flags=re.I,
        )

        if bare_code and "field_number" not in explicit:
            explicit["code"] = bare_code.group(1).upper()
            explicit["codes"] = [explicit["code"]]

    if "codes" not in explicit:
        requested_codes = extract_requested_code_tokens(
            question,
            field_numbers=entities.field_numbers,
        )

        if requested_codes:
            explicit["codes"] = requested_codes

            if len(requested_codes) == 1 and "code" not in explicit:
                explicit["code"] = requested_codes[0]

    if re.search(r"\breversal|reversals|annulation|annulations\b", normalized):
        explicit["concept"] = "reversal"

    if re.search(r"\bmessage matching\b", normalized):
        explicit["concept"] = "message matching"

    if re.search(r"\bstip\b", normalized):
        explicit["concept"] = "STIP"

    function_name = extract_function_name(question)

    if function_name:
        explicit["function_name"] = function_name

    return explicit


def field_history_is_ambiguous(state: ConversationMemoryState) -> bool:
    recent_fields = unique(state.recent_entities.get("field_numbers", []))
    active_field = state.active_entities.get("field_number")

    return not active_field and len(recent_fields) > 1


REFERENCE_MARKER_PATTERN = re.compile(
    r"\b("
    r"il|elle|lui|son|sa|ses|celui-ci|celui-la|celui-là|celle-ci|celle-la|celle-là|"
    r"ce champ|ce field|ce code|le precedent|le prÃ©cÃ©dent|la precedente|la prÃ©cÃ©dente|"
    r"en parle-t-on"
    r")\b",
    flags=re.I,
)

PROPERTY_MARKER_PATTERN = re.compile(
    r"\b("
    r"structure|format|longueur|length|type|encodage|encoding|attributs?|attributes?|"
    r"obligatoire|required|mandatory|usage|utilite|utilitÃ©|utilise|utilisÃ©|utilisee|utilisÃ©e|"
    r"present|prÃ©sent|presente|prÃ©sente|messages?|valeurs?|values?|codes?|description"
    r")\b",
    flags=re.I,
)

ELLIPTICAL_FOLLOWUP_PATTERN = re.compile(
    r"^(?:et\s+)?(?:la\s+|le\s+|les\s+|l'|des\s+)?"
    r"(structure|format|longueur|length|type|encodage|encoding|"
    r"attributs?|attributes?|usage|utilite|utilitÃ©|valeurs?|values?|codes?|"
    r"messages?|description|obligatoire|required|mandatory)"
    r"\s*\??$",
    flags=re.I,
)


FUNCTION_REFERENCE_MARKER_PATTERN = re.compile(
    r"\b(cette\s+fonction|la\s+fonction|ce\s+traitement|cette\s+procedure|cette\s+proc)\b",
    flags=re.I,
)
FUNCTION_PROPERTY_MARKER_PATTERN = re.compile(
    r"\b(exceptions?|erreurs?|errors?|exemples?|examples?|concret|role|usage|description)\b",
    flags=re.I,
)
FUNCTION_ELLIPTICAL_FOLLOWUP_PATTERN = re.compile(
    r"^(?:et\s+)?(?:les\s+|des\s+|un\s+|une\s+)?"
    r"(exceptions?|erreurs?|errors?|exemples?|examples?|exemple\s+concret|cas\s+concret)"
    r"\s*\??$",
    flags=re.I,
)


def has_contextual_reference(normalized: str) -> bool:
    return bool(
        REFERENCE_MARKER_PATTERN.search(normalized)
        or FUNCTION_REFERENCE_MARKER_PATTERN.search(normalized)
    )


def has_property_marker(normalized: str) -> bool:
    return bool(
        PROPERTY_MARKER_PATTERN.search(normalized)
        or FUNCTION_PROPERTY_MARKER_PATTERN.search(normalized)
    )


def is_elliptical_followup(normalized: str) -> bool:
    return bool(
        ELLIPTICAL_FOLLOWUP_PATTERN.search(normalized)
        or FUNCTION_ELLIPTICAL_FOLLOWUP_PATTERN.search(normalized)
    )


SEMANTIC_FOLLOWUP_ACTION_PATTERN = re.compile(
    r"\b("
    r"necessaire|n.cessaire|requis|r.quis|required|mandatory|toujours|absent|omet|omettre|renseigner|"
    r"presence|pr.sence|contraintes?|conditions?|situations?|cas|pareil|meme|se\s+passe|"
    r"utilisation|utilise|utilis"
    r")\b",
    flags=re.I,
)

SEMANTIC_FOLLOWUP_REFERENCE_PATTERN = re.compile(
    r"\b(il|elle|lui|son|sa|ses|celui-ci|celui-la|ce\s+champ|ce\s+field|l\s*omet|le\s+renseigner)\b",
    flags=re.I,
)

SEMANTIC_FOLLOWUP_OPENING_PATTERN = re.compile(
    r"^(?:"
    r"y\s+a-t-il|peut-il|peut-on|doit-on|est-ce|que\s+se\s+passe-t-il|"
    r"dans\s+quels\s+cas|quelles?\s+sont\s+les\s+(?:contraintes|conditions)"
    r")\b",
    flags=re.I,
)


OBJECT_REFERENCE_PATTERN = re.compile(
    r"\b("
    r"ces\s+codes?|ces\s+valeurs?|cette\s+table|ce\s+tableau|cette\s+liste|"
    r"ces\s+regles?|ces\s+r.gles?|ces\s+conditions?|ces\s+champs?|ce\s+resultat|ce\s+r.sultat"
    r")\b",
    flags=re.I,
)


def is_object_reference_followup(normalized: str) -> bool:
    return bool(OBJECT_REFERENCE_PATTERN.search(normalized))


def is_semantic_contextual_followup(normalized: str) -> bool:
    if SEMANTIC_FOLLOWUP_REFERENCE_PATTERN.search(normalized) and SEMANTIC_FOLLOWUP_ACTION_PATTERN.search(normalized):
        return True

    if SEMANTIC_FOLLOWUP_OPENING_PATTERN.search(normalized) and SEMANTIC_FOLLOWUP_ACTION_PATTERN.search(normalized):
        return True

    return False


def is_context_dependent_followup(normalized: str) -> bool:
    if is_object_reference_followup(normalized):
        return True

    if has_contextual_reference(normalized) and has_property_marker(normalized):
        return True

    if is_elliptical_followup(normalized):
        return True

    if is_semantic_contextual_followup(normalized):
        return True

    return bool(re.search(r"\b(et\s+dans|dans\s+quels\s+messages|quels\s+sont\s+ses)\b", normalized))


def active_context_entities(state: ConversationMemoryState) -> dict[str, Any]:
    active_entities = state.active_entities or {}
    context: dict[str, Any] = {}

    for key in ("field_number", "function_name", "message_type", "concept", "chapter", "section"):
        if active_entities.get(key):
            context[key] = active_entities[key]

    if not context and state.active_entity:
        context.update(entity_to_entities(state.active_entity))

    return context


def entities_from_active_object(active_object: dict[str, Any] | None) -> dict[str, Any]:
    if not active_object:
        return {}

    entity_type = str(active_object.get("entity_type") or "")
    entity_value = active_object.get("entity_value")

    if entity_type == "field" and entity_value:
        return {"field_number": normalize_field_number(str(entity_value))}

    if entity_type == "message_type" and entity_value:
        return {"message_type": str(entity_value)}

    if entity_type == "concept" and entity_value:
        return {"concept": str(entity_value)}

    if entity_type == "chapter" and entity_value:
        return {"chapter": str(entity_value)}

    if entity_type == "section" and entity_value:
        return {"section": str(entity_value)}

    if entity_type == "function":
        return {"function_name": str(entity_value)}

    return {}


def active_object_from_query(
    *,
    normalized: str,
    entities: dict[str, Any],
    classification: ClassificationResult,
) -> dict[str, Any] | None:
    field_number = entities.get("field_number")
    function_name = entities.get("function_name")

    if function_name:
        asks_exceptions = bool(re.search(
            r"\b(exceptions?|erreurs?|errors?|echecs?|failed|failure|nok|cas\s+d.erreur)\b",
            normalized,
        ))
        asks_examples = bool(re.search(
            r"\b(exemples?|examples?|concret|scenario|cas\s+concret)\b",
            normalized,
        ))

        if asks_exceptions:
            return {
                "type": "function_details",
                "entity_type": "function",
                "entity_value": str(function_name),
                "content_kind": "exceptions",
            }

        if asks_examples:
            return {
                "type": "function_details",
                "entity_type": "function",
                "entity_value": str(function_name),
                "content_kind": "examples",
            }

    if not field_number:
        return None

    asks_codes_or_values = bool(re.search(
        r"\b(codes?|valeurs?|values?|significations?|tous\s+ses\s+codes|tous\s+les\s+codes|toutes\s+les\s+valeurs)\b",
        normalized,
    ))
    asks_rules = bool(re.search(r"\b(regles?|r.gles?|conditions?|reject\s+codes?|codes?\s+de\s+rejet)\b", normalized))

    if classification.intent == "TABLE_LOOKUP" or asks_codes_or_values:
        return {
            "type": "table",
            "entity_type": "field",
            "entity_value": normalize_field_number(str(field_number)),
            "content_kind": "codes",
        }

    if asks_rules:
        return {
            "type": "rules",
            "entity_type": "field",
            "entity_value": normalize_field_number(str(field_number)),
            "content_kind": "rules",
        }

    return None


def context_history_is_ambiguous(state: ConversationMemoryState) -> bool:
    if active_context_entities(state):
        return False

    recent_entities = state.recent_entities or {}
    candidates = (
        len(unique(recent_entities.get("field_numbers", [])))
        + len(unique(recent_entities.get("concepts", [])))
        + len(unique(recent_entities.get("message_types", [])))
        + len(unique(recent_entities.get("function_names", [])))
    )

    return candidates > 1


def entity_identity(entity: dict[str, Any] | None) -> tuple[str, str] | None:
    if not entity:
        return None

    entity_type = str(entity.get("type") or "")
    value = str(entity.get("value") or "")

    if not entity_type or not value:
        return None

    return entity_type, value


def primary_entity_from_entities(entities: dict[str, Any]) -> dict[str, Any] | None:
    priorities = (
        ("field_number", "field"),
        ("function_name", "function"),
        ("message_type", "message_type"),
        ("concept", "concept"),
        ("chapter", "chapter"),
        ("section", "section"),
        ("command", "command"),
        ("response", "response"),
    )

    for key, entity_type in priorities:
        value = entities.get(key)

        if value:
            label = (
                f"Field {value}"
                if entity_type == "field"
                else f"Fonction {value}"
                if entity_type == "function"
                else f"Message {value}"
                if entity_type == "message_type"
                else str(value)
            )
            return {
                "type": entity_type,
                "value": str(value),
                "label": label,
            }

    return None


def entity_to_entities(entity: dict[str, Any] | None) -> dict[str, Any]:
    if not entity:
        return {}

    entity_type = entity.get("type")
    value = entity.get("value")

    if not entity_type or value is None:
        return {}

    if entity_type == "field":
        return {"field_number": str(value)}

    if entity_type == "message_type":
        return {"message_type": str(value)}

    if entity_type == "concept":
        return {"concept": str(value)}

    if entity_type == "chapter":
        return {"chapter": str(value)}

    if entity_type == "section":
        return {"section": str(value)}

    return {str(entity_type): str(value)}


def append_entity_history(
    history: list[dict[str, Any]],
    entity: dict[str, Any] | None,
    max_values: int = 12,
) -> list[dict[str, Any]]:
    identity = entity_identity(entity)

    if not identity:
        return list(history or [])[-max_values:]

    cleaned = [
        item
        for item in history or []
        if entity_identity(item)
    ]

    if cleaned and entity_identity(cleaned[-1]) == identity:
        return cleaned[-max_values:]

    cleaned.append(dict(entity or {}))
    return cleaned[-max_values:]


def entity_label(entity: dict[str, Any] | None) -> str | None:
    if not entity:
        return None

    if entity.get("label"):
        return str(entity["label"])

    entity_type = entity.get("type")
    value = entity.get("value")

    if not entity_type or value is None:
        return None

    if entity_type == "field":
        return f"Field {value}"

    if entity_type == "function":
        return f"fonction {value}"

    if entity_type == "message_type":
        return f"message {value}"

    return str(value)


def previous_entity_before(
    history: list[dict[str, Any]],
    target: dict[str, Any],
) -> dict[str, Any] | None:
    target_identity = entity_identity(target)

    if not target_identity:
        return None

    cleaned = [
        item
        for item in history or []
        if entity_identity(item)
    ]

    for index in range(len(cleaned) - 1, -1, -1):
        if entity_identity(cleaned[index]) == target_identity:
            if index > 0:
                return cleaned[index - 1]
            return None

    return None


class ConversationQueryRouter:
    """Classifie la question conversationnelle avant le RAG documentaire."""

    @staticmethod
    def route(question: str, explicit_entities: dict[str, Any]) -> str:
        normalized = normalize_for_search(question)

        asks_recall = bool(re.search(
            r"\b("
            r"quel\s+(?:field|champ|sujet).*?(?:avant|precedent)|"
            r"quel\s+est\s+(?:le\s+)?(?:field|champ|sujet).*?(?:actif|actuellement\s+actif)|"
            r"quel\s+(?:field|champ|sujet).*?(?:actif|actuellement\s+actif)|"
            r"quel\s+etait\s+le\s+precedent|"
            r"(?:field|champ|sujet)\s+precedent|"
            r"sur\s+quel\s+(?:field|champ|sujet).*?(?:avant|precedent)|"
            r"avant\s+(?:le\s+)?(?:field|champ)|"
            r"juste\s+avant|"
            r"et\s+avant|"
            r"et\s+encore\s+avant|"
            r"quels?\s+(?:fields?|champs?)\s+avons-nous\s+analys|"
            r"dans\s+quel\s+ordre|"
            r"quel\s+etait\s+le\s+premier"
            r")\b",
            normalized,
        ))

        if not asks_recall:
            asks_recall = bool(re.search(
                r"^(?:quels?|quelle?s?)\s+(?:fields?|champs?|sujets?)\s+avons-nous\s+analys",
                normalized,
            ))

        if not asks_recall:
            asks_recall = bool(re.search(r"^dans\s+quel\s+ordre\b", normalized))

        if asks_recall:
            return "CONVERSATION_RECALL"

        if not explicit_entities and is_context_dependent_followup(normalized):
            return "CONTEXTUAL_DOCUMENT_QA"

        return "STANDALONE_DOCUMENT_QA"


def build_recall_answer(
    question: str,
    state: ConversationMemoryState,
    explicit_entities: dict[str, Any],
) -> tuple[str | None, dict[str, Any], str | None, int | None]:
    normalized = normalize_for_search(question)
    history = [
        item
        for item in (state.entity_history or [])
        if entity_identity(item)
    ]
    target_entity = primary_entity_from_entities(explicit_entities)
    inherited: dict[str, Any] = {}
    answer_entity = None
    recall_cursor: int | None = None
    answer_prefix: str | None = None

    if not history and not state.active_entity:
        return None, inherited, "CONVERSATION_CONTEXT_AMBIGUOUS", None

    asks_history_list = bool(re.search(
        r"\b(quels?\s+(?:fields?|champs?)\s+avons-nous\s+analys|dans\s+quel\s+ordre)\b",
        normalized,
    ))

    if not asks_history_list:
        asks_history_list = bool(re.search(
            r"^(?:quels?|quelle?s?)\s+(?:fields?|champs?|sujets?)\s+avons-nous\s+analys",
            normalized,
        ))

    if not asks_history_list:
        asks_history_list = bool(re.search(r"^dans\s+quel\s+ordre\b", normalized))

    if asks_history_list:
        labels = [
            label
            for label in (entity_label(item) for item in history)
            if label
        ]

        if not labels:
            return None, inherited, "CONVERSATION_CONTEXT_AMBIGUOUS", None

        return " -> ".join(labels) + ".", inherited, None, state.recall_cursor

    asks_active = bool(re.search(r"\b(actif|actuellement\s+actif)\b", normalized))

    if asks_active:
        answer_entity = state.active_entity or (history[-1] if history else None)
        recall_cursor = len(history) - 1 if history else state.recall_cursor
        answer_prefix = (
            "Le Field actuellement actif est le"
            if re.search(r"\b(field|champ)\b", normalized)
            else "Le sujet actuellement actif est"
        )
    elif re.search(r"\bpremier\b", normalized):
        answer_entity = history[0] if history else None
        recall_cursor = 0 if history else None
    elif re.search(r"\bet\s+(?:encore\s+)?avant\b", normalized) and state.recall_cursor is not None:
        next_cursor = state.recall_cursor - 1
        if next_cursor >= 0 and next_cursor < len(history):
            answer_entity = history[next_cursor]
            recall_cursor = next_cursor
    elif re.search(r"\bavant\b|\bjuste\s+avant\b", normalized):
        if target_entity:
            answer_entity = previous_entity_before(history, target_entity)
        elif state.active_entity:
            answer_entity = state.previous_entity or previous_entity_before(history, state.active_entity)
        elif len(history) >= 2:
            answer_entity = history[-2]

        if answer_entity:
            recall_cursor = next(
                (
                    index
                    for index, item in enumerate(history)
                    if entity_identity(item) == entity_identity(answer_entity)
                ),
                None,
            )
    elif re.search(r"\bprecedent\b", normalized):
        answer_entity = state.previous_entity or (history[-2] if len(history) >= 2 else None)
        if answer_entity:
            recall_cursor = next(
                (
                    index
                    for index, item in enumerate(history)
                    if entity_identity(item) == entity_identity(answer_entity)
                ),
                None,
            )
    else:
        answer_entity = state.active_entity or (history[-1] if history else None)
        recall_cursor = len(history) - 1 if history else state.recall_cursor

    if not answer_entity:
        return None, inherited, "CONVERSATION_CONTEXT_AMBIGUOUS", state.recall_cursor

    inherited.update(entity_to_entities(answer_entity))
    label = entity_label(answer_entity)

    if not label:
        return None, inherited, "CONVERSATION_CONTEXT_AMBIGUOUS", recall_cursor

    if answer_prefix:
        return f"{answer_prefix} {label}.", inherited, None, recall_cursor

    return f"{label}.", inherited, None, recall_cursor


def merge_recent_entity(
    existing: dict[str, list[str]],
    key: str,
    value: str | None,
    max_values: int = 5,
) -> None:
    if not value:
        return

    values = [value, *existing.get(key, [])]
    existing[key] = unique(values)[:max_values]


class ConversationMemoryStore:
    """Small Mongo-backed store for the active conversation context."""

    @staticmethod
    async def get_state(conversation_id: str) -> ConversationMemoryState:
        document = await conversation_memory_states_collection.find_one(
            {"conversation_id": conversation_id}
        )

        if not document:
            return ConversationMemoryState(conversation_id=conversation_id)

        document.pop("_id", None)
        return ConversationMemoryState.model_validate(document)

    @staticmethod
    async def upsert_state(state: ConversationMemoryState) -> None:
        payload = state.model_dump()

        await conversation_memory_states_collection.update_one(
            {"conversation_id": state.conversation_id},
            {"$set": payload},
            upsert=True,
        )

    @staticmethod
    async def recent_messages(
        conversation_id: str,
        limit: int = MAX_RECENT_MESSAGES,
        max_characters: int = MAX_MEMORY_CHARACTERS,
    ) -> list[ConversationMemoryMessage]:
        cursor = messages_collection.find(
            {"conversation_id": conversation_id}
        ).sort("created_at", -1)

        documents = await cursor.to_list(length=limit * 2)
        selected: list[ConversationMemoryMessage] = []
        total_characters = 0

        for document in documents:
            content = clean_text(document.get("content", ""))
            total_characters += len(content)

            if total_characters > max_characters and selected:
                break

            structured = document.get("structured") or {}
            selected.append(
                ConversationMemoryMessage(
                    role=str(document.get("role") or ""),
                    content=content,
                    timestamp=document.get("created_at"),
                    referenced_document_ids=attachment_document_ids(document),
                    intent=structured.get("intent"),
                    entities=structured.get("entities") or {},
                )
            )

        return list(reversed(selected))


class ConversationContextResolver:
    """Resolve short follow-up questions before the documentation classifier."""

    @staticmethod
    def resolve(
        question: str,
        state: ConversationMemoryState,
        recent_messages: list[ConversationMemoryMessage] | None = None,
    ) -> ResolvedConversationQuery:
        started = time.perf_counter()
        explicit = extract_current_entities(question)
        inherited: dict[str, Any] = {}
        normalized = normalize_for_search(question)
        query_type = ConversationQueryRouter.route(question, explicit)
        used_memory = False
        ambiguity = False
        ambiguity_reason = None

        if query_type == "CONVERSATION_RECALL":
            recall_answer, recall_entities, recall_ambiguity, recall_cursor = build_recall_answer(
                question,
                state,
                explicit,
            )
            inherited.update(recall_entities)
            used_memory = bool(recall_answer)
            ambiguity = bool(recall_ambiguity)
            ambiguity_reason = recall_ambiguity

            return ResolvedConversationQuery(
                original_query=question,
                resolved_query=question,
                query_type=query_type,
                recall_answer=recall_answer,
                recall_cursor=recall_cursor,
                inherited_entities=inherited,
                explicit_entities=explicit,
                resolution_confidence=0.9 if recall_answer else 0.45,
                used_memory=used_memory,
                ambiguity_detected=ambiguity,
                ambiguity_reason=ambiguity_reason,
                recent_messages_count=len(recent_messages or []),
                summary_used=bool(state.summary and used_memory),
                resolution_ms=round((time.perf_counter() - started) * 1000),
            )
        
        active_entities = dict(state.active_entities or {})

        for key, value in active_context_entities(state).items():
            active_entities.setdefault(key, value)

        explicit_context_keys = {
            "field_number",
            "function_name",
            "message_type",
            "concept",
            "chapter",
            "section",
        }
        has_explicit_context = any(key in explicit for key in explicit_context_keys)
        followup_requires_context = is_context_dependent_followup(normalized)
        object_reference_followup = is_object_reference_followup(normalized)

        if (
            "function_name" not in explicit
            and not has_explicit_context
            and active_entities.get("function_name")
            and FUNCTION_PROPERTY_MARKER_PATTERN.search(normalized)
        ):
            inherited["function_name"] = active_entities["function_name"]
            used_memory = True

        if (
            explicit.get("function_name")
            and state.active_object
            and state.active_object.get("entity_type") == "function"
            and not FUNCTION_PROPERTY_MARKER_PATTERN.search(normalized)
            and re.search(r"\b(et\s+pour|pour|et)\b", normalized)
        ):
            content_kind = state.active_object.get("content_kind")

            if content_kind:
                inherited["function_topic"] = str(content_kind)
                used_memory = True

        if "field_number" not in explicit and "code" in explicit:
            if field_history_is_ambiguous(state):
                ambiguity = True
                ambiguity_reason = "multiple_recent_fields_without_active_field"
            elif active_entities.get("field_number"):
                inherited["field_number"] = active_entities["field_number"]
                used_memory = True

        if (
            "field_number" not in explicit
            and "codes" in explicit
            and "field_number" not in inherited
        ):
            if field_history_is_ambiguous(state):
                ambiguity = True
                ambiguity_reason = "multiple_recent_fields_without_active_field"
            elif active_entities.get("field_number"):
                inherited["field_number"] = active_entities["field_number"]
                used_memory = True

        if object_reference_followup and not has_explicit_context:
            object_entities = entities_from_active_object(state.active_object)

            if object_entities:
                inherited.update(object_entities)
                used_memory = True
            else:
                ambiguity = True
                ambiguity_reason = "CONVERSATION_OBJECT_CONTEXT_AMBIGUOUS"

        if followup_requires_context and not has_explicit_context and not object_reference_followup:
            contextual_entities = active_context_entities(state)

            if contextual_entities:
                inherited.update(contextual_entities)
                used_memory = True
            elif context_history_is_ambiguous(state):
                ambiguity = True
                ambiguity_reason = "CONVERSATION_CONTEXT_AMBIGUOUS"
            else:
                ambiguity = True
                ambiguity_reason = "CONVERSATION_CONTEXT_AMBIGUOUS"

        if "concept" not in explicit:
            if re.search(r"\b(ou|où|where)\b.*\b(parle|mentioned|trouve)\b", normalized):
                if active_entities.get("concept"):
                    inherited["concept"] = active_entities["concept"]
                    used_memory = True
            elif re.search(r"\b(pourquoi|why|celui-ci|ce field|ce code|precedent|précédent)\b", normalized):
                for key in ("field_number", "code", "function_name", "message_type", "concept"):
                    if active_entities.get(key):
                        inherited[key] = active_entities[key]
                        used_memory = True

        if re.search(r"\b(autres|tous les codes|toutes les valeurs|donne-moi les autres)\b", normalized):
            if "field_number" not in explicit and active_entities.get("field_number"):
                inherited["field_number"] = active_entities["field_number"]
                used_memory = True

        if re.search(r"\breject codes?|codes? de rejet|rejets?\b", normalized):
            if "field_number" not in explicit and active_entities.get("field_number"):
                inherited["field_number"] = active_entities["field_number"]
                used_memory = True

        compare_field = re.search(
            r"\b(?:compare|comparer|avec|vs)\s+(?:field\s*)?0*(\d{3}(?:\.\d+)?)\b",
            normalized,
        )

        if compare_field:
            explicit["compare_field_number"] = normalize_field_number(compare_field.group(1))

            if "field_number" not in explicit and active_entities.get("field_number"):
                inherited["field_number"] = active_entities["field_number"]
                used_memory = True

        effective_entities = {**inherited, **explicit}
        resolved_query = ConversationContextResolver.build_resolved_query_v2(
            original_query=question,
            entities=effective_entities,
            normalized=normalized,
        )

        if resolved_query == question and used_memory:
            resolved_query = f"{question} Contexte: {ConversationContextResolver.describe_entities(inherited)}"

        confidence = 0.55 if ambiguity else 0.92 if used_memory else 1.0

        return ResolvedConversationQuery(
            original_query=question,
            resolved_query=resolved_query,
            query_type=query_type,
            inherited_entities=inherited,
            explicit_entities=explicit,
            resolution_confidence=confidence,
            used_memory=used_memory,
            ambiguity_detected=ambiguity,
            ambiguity_reason=ambiguity_reason,
            recent_messages_count=len(recent_messages or []),
            summary_used=bool(state.summary and used_memory),
            resolution_ms=round((time.perf_counter() - started) * 1000),
        )

    @staticmethod
    def describe_entities(entities: dict[str, Any]) -> str:
        parts = []

        if entities.get("field_number"):
            parts.append(f"Field {entities['field_number']}")

        if entities.get("code"):
            parts.append(f"code {entities['code']}")

        if entities.get("function_name"):
            parts.append(f"fonction {entities['function_name']}")

        if entities.get("concept"):
            parts.append(str(entities["concept"]))

        return ", ".join(parts)

    @staticmethod
    def build_resolved_query_v2(
        *,
        original_query: str,
        entities: dict[str, Any],
        normalized: str,
    ) -> str:
        field_number = entities.get("field_number")
        function_name = entities.get("function_name")
        function_topic = entities.get("function_topic")
        code = entities.get("code")
        codes = [
            str(value).upper()
            for value in entities.get("codes", []) or []
            if value
        ]
        concept = entities.get("concept")
        compare_field = entities.get("compare_field_number")
        message_type = entities.get("message_type")

        if function_name and (
            function_topic == "exceptions"
            or re.search(r"\b(exceptions?|erreurs?|errors?|cas\s+d.erreur)\b", normalized)
        ):
            return f"Quelles sont les exceptions de la fonction {function_name} ?"

        if function_name and (
            function_topic == "examples"
            or re.search(r"\b(exemples?|examples?|concret|scenario|cas\s+concret)\b", normalized)
        ):
            return f"Donne un exemple concret pour la fonction {function_name}."

        if function_name and re.search(r"\b(role|usage|description|explique|explique-moi)\b", normalized):
            return f"Explique la fonction {function_name}."

        if function_name and is_context_dependent_followup(normalized):
            return f"Reponds a la question suivante pour la fonction {function_name}: {original_query}"

        if compare_field and field_number:
            return f"Compare le Field {field_number} avec le Field {compare_field}."

        if field_number and len(codes) > 1:
            codes_text = ", ".join(codes)

            if re.search(r"\b(compare|comparer|comparaison)\b", normalized):
                return (
                    f"Compare les codes {codes_text} du Field {field_number} "
                    "avec leurs significations documentaires."
                )

            return (
                f"{original_query} Cible documentaire: Field {field_number}. "
                f"Codes demandes: {codes_text}."
            )

        if field_number and code:
            return f"Que signifie Field {field_number} = {code} ?"

        if field_number and re.search(r"\b(ces\s+codes?|ces\s+valeurs?)\b", normalized):
            return f"Donne-moi les codes du Field {field_number} et leurs significations."

        if field_number and re.search(r"\b(autres|tous les codes|tous\s+ses\s+codes|toutes les valeurs|toutes\s+ses\s+valeurs|donne-moi les autres)\b", normalized):
            return f"Quels sont tous les codes ou valeurs du Field {field_number} ?"

        if field_number and re.search(r"\breject codes?|codes? de rejet|rejets?\b", normalized):
            return f"Quels sont les reject codes du Field {field_number} ?"

        if field_number and re.search(r"\b(structure|compose|compos|positions?)\b", normalized):
            return f"Quelle est la structure du Field {field_number} ?"

        if field_number and re.search(r"\b(format|type|encodage|encoding)\b", normalized):
            return f"Quel est le format du Field {field_number} ?"

        if field_number and re.search(r"\b(longueur|length|taille)\b", normalized):
            return f"Quelle est la longueur du Field {field_number} ?"

        if field_number and re.search(r"\b(attributs?|attributes?)\b", normalized):
            return f"Quels sont les attributs du Field {field_number} ?"

        if field_number and re.search(r"\b(situations?|cas|presence|pr.sence|necessaire|n.cessaire|toujours|requis|r.quis|required)\b", normalized) and re.search(r"\b(presence|pr.sence|necessaire|n.cessaire|pas\s+n.cessaire|n\s+est\s+pas|requise|r.quise|required|toujours)\b", normalized):
            return f"Y a-t-il des situations ou la presence du Field {field_number} n'est pas necessaire ?"

        if field_number and re.search(r"\b(absent|omet|omettre|renseigner)\b", normalized):
            return f"Le Field {field_number} peut-il etre absent ou omis ?"

        if field_number and re.search(r"\b(contraintes?|conditions?)\b", normalized):
            return f"Quelles sont les contraintes ou conditions liees a l'utilisation du Field {field_number} ?"

        if field_number and re.search(r"\b(pareil|meme)\b", normalized):
            return f"Est-ce pareil pour le Field {field_number} ?"

        if field_number and re.search(r"\b(obligatoire|required|mandatory)\b", normalized):
            return f"Le Field {field_number} est-il obligatoire ?"

        if field_number and re.search(r"\b(messages?|utilise|utilis|usage|present|presente)\b", normalized):
            return f"Dans quels messages le Field {field_number} est-il utilise ?"

        if field_number and re.search(r"\b(utilite|role)\b", normalized):
            return f"Quelle est l'utilite du Field {field_number} ?"

        if field_number and re.search(r"\b(codes?|valeurs?|values?)\b", normalized):
            return f"Quels sont les codes ou valeurs du Field {field_number} ?"

        if field_number and re.search(r"\b(et pour|pour)\b", normalized):
            return f"Que represente le Field {field_number} ?"

        if field_number and is_context_dependent_followup(normalized):
            return f"Reponds a la question suivante pour le Field {field_number}: {original_query}"

        if message_type and re.search(r"\b(messages?|utilise|utilis|usage|cas|quand|when)\b", normalized):
            return f"Dans quels cas le message {message_type} est-il utilise ?"

        if message_type and is_context_dependent_followup(normalized):
            return f"Reponds a la question suivante pour le message {message_type}: {original_query}"

        if concept and re.search(r"\b(ou|where)\b.*\b(parle|mentioned|trouve)\b", normalized):
            return f"Où parle-t-on de {concept} dans la documentation ?"

        if concept == "reversal" and re.search(r"\b(et|pour|dans)\b", normalized):
            return "Explique les reversals dans ce contexte documentaire."

        return original_query

    @staticmethod
    def build_resolved_query(
        *,
        original_query: str,
        entities: dict[str, Any],
        normalized: str,
    ) -> str:
        field_number = entities.get("field_number")
        code = entities.get("code")
        concept = entities.get("concept")
        compare_field = entities.get("compare_field_number")

        if compare_field and field_number:
            return f"Compare le Field {field_number} avec le Field {compare_field}."

        if field_number and code:
            return f"Que signifie Field {field_number} = {code} ?"

        if field_number and re.search(r"\b(autres|tous les codes|toutes les valeurs|donne-moi les autres)\b", normalized):
            return f"Quels sont tous les codes ou valeurs du Field {field_number} ?"

        if field_number and re.search(r"\breject codes?|codes? de rejet|rejets?\b", normalized):
            return f"Quels sont les reject codes du Field {field_number} ?"

        if field_number and re.search(r"\b(et pour|pour)\b", normalized):
            return f"Que représente le Field {field_number} ?"

        if concept and re.search(r"\b(ou|où|where)\b.*\b(parle|mentioned|trouve)\b", normalized):
            return f"Où parle-t-on de {concept} dans la documentation ?"

        if concept == "reversal" and re.search(r"\b(et|pour|dans)\b", normalized):
            return "Explique les reversals dans ce contexte documentaire."

        return original_query


def state_from_resolution(
    *,
    state: ConversationMemoryState,
    resolved: ResolvedConversationQuery,
    classification: ClassificationResult,
    referenced_document_ids: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> ConversationMemoryState:
    if resolved.query_type == "CONVERSATION_RECALL":
        return ConversationMemoryState(
            conversation_id=state.conversation_id,
            active_document_ids=unique([
                *(referenced_document_ids or []),
                *state.active_document_ids,
            ]),
            active_topic=state.active_topic,
            active_entities=state.active_entities,
            active_entity=state.active_entity,
            previous_entity=state.previous_entity,
            entity_history=state.entity_history,
            active_object=state.active_object,
            recall_cursor=resolved.recall_cursor if resolved.recall_cursor is not None else state.recall_cursor,
            recent_entities=state.recent_entities,
            last_intent=classification.intent,
            last_resolved_query=resolved.resolved_query,
            last_evidence_refs=evidence_refs or state.last_evidence_refs,
            summary=state.summary,
            updated_at=datetime.now(timezone.utc),
        )

    active_entities = dict(state.active_entities or {})
    recent_entities = {
        key: list(value)
        for key, value in (state.recent_entities or {}).items()
    }
    merged_entities = {
        **resolved.inherited_entities,
        **resolved.explicit_entities,
    }

    for key in ("field_number", "code", "function_name", "message_type", "concept", "chapter", "section"):
        value = merged_entities.get(key)

        if value:
            active_entities[key] = value

    if resolved.explicit_entities.get("field_number"):
        active_entities.pop("code", None)
        active_entities.pop("function_name", None)

    if resolved.explicit_entities.get("concept") and "field_number" not in resolved.explicit_entities:
        if resolved.explicit_entities["concept"] not in {"reversal"}:
            active_entities.pop("field_number", None)
            active_entities.pop("code", None)
            active_entities.pop("function_name", None)

    if resolved.explicit_entities.get("function_name"):
        active_entities.pop("field_number", None)
        active_entities.pop("code", None)
        active_entities.pop("concept", None)

    merge_recent_entity(
        recent_entities,
        "field_numbers",
        active_entities.get("field_number"),
    )
    merge_recent_entity(
        recent_entities,
        "codes",
        active_entities.get("code"),
    )
    merge_recent_entity(
        recent_entities,
        "concepts",
        active_entities.get("concept"),
    )
    merge_recent_entity(
        recent_entities,
        "function_names",
        active_entities.get("function_name"),
    )

    active_document_ids = unique([
        *(referenced_document_ids or []),
        *state.active_document_ids,
    ])

    active_topic = active_entities.get("concept")

    if active_entities.get("field_number"):
        active_topic = f"Field {active_entities['field_number']}"

    if active_entities.get("function_name"):
        active_topic = f"Fonction {active_entities['function_name']}"

    old_active_entity = state.active_entity or primary_entity_from_entities(
        state.active_entities or {}
    )
    new_active_entity = primary_entity_from_entities(active_entities)
    previous_entity = state.previous_entity
    entity_history = list(state.entity_history or [])
    normalized_question = normalize_for_search(
        f"{resolved.original_query} {resolved.resolved_query}"
    )
    new_active_object = active_object_from_query(
        normalized=normalized_question,
        entities=active_entities,
        classification=classification,
    )

    if entity_identity(new_active_entity) != entity_identity(old_active_entity):
        if old_active_entity:
            previous_entity = old_active_entity
        entity_history = append_entity_history(entity_history, new_active_entity)
    else:
        entity_history = append_entity_history(entity_history, new_active_entity)

    active_object = new_active_object

    if not active_object and entity_identity(new_active_entity) == entity_identity(old_active_entity):
        active_object = state.active_object

    summary = build_memory_summary(
        active_topic=active_topic,
        active_entities=active_entities,
        last_intent=classification.intent,
        last_resolved_query=resolved.resolved_query,
    )

    return ConversationMemoryState(
        conversation_id=state.conversation_id,
        active_document_ids=active_document_ids,
        active_topic=active_topic,
        active_entities=active_entities,
        active_entity=new_active_entity,
        previous_entity=previous_entity,
        entity_history=entity_history,
        active_object=active_object,
        recall_cursor=None,
        recent_entities=recent_entities,
        last_intent=classification.intent,
        last_resolved_query=resolved.resolved_query,
        last_evidence_refs=evidence_refs or state.last_evidence_refs,
        summary=summary,
        updated_at=datetime.now(timezone.utc),
    )


def build_memory_summary(
    *,
    active_topic: str | None,
    active_entities: dict[str, Any],
    last_intent: str,
    last_resolved_query: str,
) -> str:
    parts = []

    if active_topic:
        parts.append(f"Sujet actif: {active_topic}.")

    if active_entities:
        compact_entities = {
            key: value
            for key, value in active_entities.items()
            if key in {"field_number", "code", "function_name", "message_type", "concept", "chapter", "section"}
        }

        if compact_entities:
            parts.append(f"Entites actives: {compact_entities}.")

    parts.append(f"Derniere intention: {last_intent}.")
    parts.append(f"Derniere question resolue: {last_resolved_query}.")

    return " ".join(parts)


async def resolve_documentation_query(
    *,
    question: str,
    conversation_id: str | None,
) -> tuple[ResolvedConversationQuery, ConversationMemoryState, list[ConversationMemoryMessage]]:
    if not conversation_id or not conversation_memory_enabled():
        return (
            ResolvedConversationQuery(
                original_query=question,
                resolved_query=question,
            ),
            ConversationMemoryState(conversation_id=conversation_id or ""),
            [],
        )

    state = await ConversationMemoryStore.get_state(conversation_id)
    recent_messages = await ConversationMemoryStore.recent_messages(conversation_id)

    resolved = ConversationContextResolver.resolve(
        question=question,
        state=state,
        recent_messages=recent_messages,
    )

    logger.debug(
        "DOCUMENTATION_MEMORY_RESOLUTION memory_used=%s confidence=%s inherited=%s explicit=%s ambiguity=%s recent_messages=%s ms=%s",
        resolved.used_memory,
        resolved.resolution_confidence,
        resolved.inherited_entities,
        resolved.explicit_entities,
        resolved.ambiguity_detected,
        resolved.recent_messages_count,
        resolved.resolution_ms,
    )

    return resolved, state, recent_messages


async def update_documentation_memory(
    *,
    conversation_id: str | None,
    state: ConversationMemoryState | None,
    resolved: ResolvedConversationQuery | None,
    referenced_document_ids: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> None:
    if not conversation_id or not conversation_memory_enabled() or not resolved:
        return

    try:
        current_state = state or await ConversationMemoryStore.get_state(conversation_id)
        classification = GenericQuestionClassifier.classify(resolved.resolved_query)
        updated_state = state_from_resolution(
            state=current_state,
            resolved=resolved,
            classification=classification,
            referenced_document_ids=referenced_document_ids,
            evidence_refs=evidence_refs,
        )
        await ConversationMemoryStore.upsert_state(updated_state)
    except Exception as error:
        logger.info("MEMORY_STATE_UPDATE_ERROR %s", error)
