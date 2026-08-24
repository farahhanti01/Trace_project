import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

from bson import ObjectId

from app.database import (
    document_sections_collection,
    documents_collection,
)
from app.services.document_service import BACKEND_ROOT
from app.services.file_type_service import (
    IMAGE_EXTENSIONS,
    REFERENCE_DOCUMENT_EXTENSIONS,
    is_image_extension,
)
from app.services.hps_ai_service import (
    HpsAiConfigurationError,
    HpsAiRequestError,
    call_hps_ai,
)
from app.services.retrieval_service import select_relevant_sections


logger = logging.getLogger(__name__)

MAX_SCREENSHOT_IMAGES = 4
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
MAX_SCREENSHOT_CONTEXT_SECTIONS = 8
SCREEN_EXTRACTION = "SCREEN_EXTRACTION"
SCREEN_UNDERSTANDING = "SCREEN_UNDERSTANDING"
SCREEN_DIAGNOSIS = "SCREEN_DIAGNOSIS"
SCREEN_VALUE_EXPLANATION = "SCREEN_VALUE_EXPLANATION"
DOCUMENTATION_LOOKUP = "DOCUMENTATION_LOOKUP"


def normalize_query_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def normalize_field_number(value: Any) -> str:
    raw = str(value or "").strip()

    if not raw:
        return ""

    if "." in raw:
        left, right = raw.split(".", 1)
        return f"{int(left):03d}.{right}" if left.isdigit() else raw

    return f"{int(raw):03d}" if raw.isdigit() else raw


def question_mentions_screenshot(question: str) -> bool:
    normalized = normalize_query_text(question)

    return any(
        term in normalized
        for term in (
            "screen",
            "screenshot",
            "capture",
            "image",
            "depuis le screen",
            "depuis la capture",
            "dans ce screen",
            "dans l'image",
            "dans la trace visible",
        )
    )


def extract_requested_field_number(question: str) -> str:
    patterns = (
        r"\bfield\s*0*(\d{1,3}(?:\.\d+)?)\b",
        r"\bfld\s*\(?0*(\d{1,3}(?:\.\d+)?)\)?",
        r"\bchamp\s*0*(\d{1,3}(?:\.\d+)?)\b",
        r"\bde\s*0*(\d{1,3}(?:\.\d+)?)\b",
    )

    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)

        if match:
            return normalize_field_number(match.group(1))

    return ""


def question_requests_mti(question: str) -> bool:
    return bool(re.search(r"\bmti\b|\bmessage type\b", question, flags=re.IGNORECASE))


def extract_mentioned_value(question: str) -> str:
    field_match = re.search(
        r"\b(?:field|fld|champ)\s*0*\d{1,3}\s*[:=]\s*([A-Za-z0-9]+)\b",
        question,
        flags=re.IGNORECASE,
    )

    if field_match:
        return field_match.group(1).upper()

    value_match = re.search(
        r"\b([A-Z]{2}\d{2}|\d{6,}|\d{2})\b",
        question,
        flags=re.IGNORECASE,
    )

    return value_match.group(1).upper() if value_match else ""


def classify_screenshot_intent(question: str) -> str:
    normalized = normalize_query_text(question)
    requested_field = extract_requested_field_number(question)
    screen_related = question_mentions_screenshot(question)
    extraction_terms = (
        "extrait",
        "extrais",
        "extract",
        "donne-moi",
        "donnez-moi",
        "quelle est la valeur",
        "valeur du",
        "affiche",
    )
    diagnosis_terms = (
        "pourquoi",
        "echoue",
        "échoue",
        "echec",
        "échec",
        "failed",
        "failure",
        "erreur",
        "pin",
        "pvv",
        "bien passe",
        "bien passé",
        "anomalie",
        "diagnostic",
    )

    if requested_field and not screen_related and any(
        term in normalized
        for term in (
            "que signifie",
            "que represente",
            "que représente",
            "valeurs possibles",
            "codes du field",
            "documentation",
        )
    ):
        return DOCUMENTATION_LOOKUP

    if (
        requested_field
        or question_requests_mti(question)
    ) and any(term in normalized for term in extraction_terms):
        return SCREEN_EXTRACTION

    if requested_field and re.search(
        r"^(?:et\s+)?(?:le\s+|la\s+)?(?:champ|field|fld)\s+\d{1,3}\s*\??$",
        normalized,
    ):
        return SCREEN_EXTRACTION

    if screen_related and (
        requested_field
        or question_requests_mti(question)
    ) and any(term in normalized for term in ("valeur", "champ", "field", "mti")):
        return SCREEN_EXTRACTION

    if any(term in normalized for term in diagnosis_terms):
        return SCREEN_DIAGNOSIS

    if (
        screen_related
        and extract_mentioned_value(question)
        and any(term in normalized for term in ("que signifie", "que represente", "que représente"))
    ):
        return SCREEN_VALUE_EXPLANATION

    if screen_related or any(
        term in normalized
        for term in (
            "explique cette partie",
            "explique ce passage",
            "que represente ce screen",
            "que représente ce screen",
            "analyse ce screen",
        )
    ):
        return SCREEN_UNDERSTANDING

    return SCREEN_UNDERSTANDING


def valid_object_ids(document_ids: list[str] | None) -> list[ObjectId]:
    return [
        ObjectId(document_id)
        for document_id in document_ids or []
        if ObjectId.is_valid(document_id)
    ]


def image_media_type(document: dict[str, Any]) -> str:
    extension = str(document.get("extension") or "").lower()

    if extension in {".jpg", ".jpeg"}:
        return "image/jpeg"

    if extension == ".webp":
        return "image/webp"

    if extension == ".bmp":
        return "image/bmp"

    if extension in {".tif", ".tiff"}:
        return "image/tiff"

    return "image/png"


def data_url_for_image(document: dict[str, Any]) -> str | None:
    relative_path = document.get("relative_path")

    if not relative_path:
        return None

    file_path = BACKEND_ROOT / relative_path

    if not file_path.exists() or file_path.stat().st_size > MAX_SCREENSHOT_BYTES:
        return None

    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")

    return f"data:{image_media_type(document)};base64,{encoded}"


def image_content_parts(
    image_documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parts = []

    for document in image_documents[:MAX_SCREENSHOT_IMAGES]:
        data_url = data_url_for_image(document)

        if not data_url:
            continue

        parts.append({
            "type": "image_url",
            "image_url": {
                "url": data_url,
                "detail": "high",
            },
        })

    return parts


async def load_referenced_images(
    referenced_document_ids: list[str] | None,
) -> list[dict[str, Any]]:
    object_ids = valid_object_ids(referenced_document_ids)

    if not object_ids:
        return []

    documents = await documents_collection.find(
        {
            "_id": {"$in": object_ids},
            "status": "extracted",
        }
    ).sort("created_at", 1).to_list(length=MAX_SCREENSHOT_IMAGES)

    return [
        document
        for document in documents
        if is_image_extension(document.get("extension"))
    ]


def should_load_conversation_images(intent: str) -> bool:
    return intent in {
        SCREEN_EXTRACTION,
        SCREEN_UNDERSTANDING,
        SCREEN_DIAGNOSIS,
        SCREEN_VALUE_EXPLANATION,
    }


async def load_conversation_images(
    *,
    conversation_id: str,
) -> list[dict[str, Any]]:
    documents = await documents_collection.find(
        {
            "conversation_id": conversation_id,
            "status": "extracted",
            "extension": {"$in": sorted(IMAGE_EXTENSIONS)},
        }
    ).sort("created_at", -1).to_list(length=MAX_SCREENSHOT_IMAGES)

    return [
        document
        for document in documents
        if is_image_extension(document.get("extension"))
    ]


def non_image_reference_extensions() -> list[str]:
    return sorted(
        extension
        for extension in REFERENCE_DOCUMENT_EXTENSIONS
        if not is_image_extension(extension)
    )


async def load_context_documents(
    *,
    conversation_id: str,
    referenced_document_ids: list[str] | None,
    agent: str,
) -> list[dict[str, Any]]:
    object_ids = valid_object_ids(referenced_document_ids)
    extensions = non_image_reference_extensions()

    documents: list[dict[str, Any]] = []

    if object_ids:
        documents = await documents_collection.find(
            {
                "_id": {"$in": object_ids},
                "status": "extracted",
                "extension": {"$in": extensions},
            }
        ).sort("created_at", 1).to_list(length=100)

    query: dict[str, Any] = {
        "conversation_id": conversation_id,
        "status": "extracted",
        "extension": {"$in": extensions},
    }

    if agent == "documentation":
        query["agent"] = "documentation"

    conversation_documents = await documents_collection.find(
        query
    ).sort("created_at", 1).to_list(length=100)

    by_id = {
        str(document["_id"]): document
        for document in [
            *conversation_documents,
            *documents,
        ]
    }

    return list(by_id.values())


async def load_context_sections(
    *,
    conversation_id: str,
    referenced_document_ids: list[str] | None,
    agent: str,
) -> list[dict[str, Any]]:
    documents = await load_context_documents(
        conversation_id=conversation_id,
        referenced_document_ids=referenced_document_ids,
        agent=agent,
    )

    if not documents:
        return []

    documents_by_id = {
        str(document["_id"]): document
        for document in documents
    }
    sections = await document_sections_collection.find(
        {
            "document_id": {
                "$in": list(documents_by_id.keys()),
            }
        }
    ).sort(
        [
            ("document_id", 1),
            ("section_index", 1),
            ("chunk_index", 1),
        ]
    ).to_list(length=2_000)

    enriched = []

    for section in sections:
        document = documents_by_id.get(section.get("document_id"))

        if not document:
            continue

        enriched.append({
            "document_id": section.get("document_id"),
            "source_section_id": str(section.get("_id")),
            "source": document.get("original_filename"),
            "text": section.get("text", ""),
            "page": section.get("page"),
            "page_document": section.get("page_document"),
            "sheet": section.get("sheet"),
            "paragraph": section.get("paragraph"),
            "heading": section.get("heading"),
            "field_number": section.get("field_number"),
            "field_name": section.get("field_name"),
            "content_type": section.get("content_type"),
            "section_index": section.get("section_index"),
            "chunk_index": section.get("chunk_index"),
            "embedding": section.get("embedding"),
        })

    return enriched


def parse_json_object(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)

        if not match:
            return {}

        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    return payload if isinstance(payload, dict) else {}


def source_id_for_index(index: int) -> str:
    return f"S{index + 1}"


def reference_from_section(
    section: dict[str, Any],
    source_id: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source": section.get("source") or "Document",
        "original_source": section.get("source"),
        "document_id": section.get("document_id"),
        "page": section.get("page"),
        "pdf_page": section.get("page"),
        "printed_page": section.get("page_document"),
        "section": section.get("heading"),
        "sheet": section.get("sheet"),
        "paragraph": section.get("paragraph"),
    }


def build_context(selected_sections: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    references = []
    chunks = []

    for index, section in enumerate(selected_sections):
        source_id = source_id_for_index(index)
        references.append(reference_from_section(section, source_id))
        metadata = [
            f"Source ID: {source_id}",
            f"Document: {section.get('source') or 'Document'}",
        ]

        if section.get("heading"):
            metadata.append(f"Section: {section['heading']}")

        if section.get("page") is not None:
            metadata.append(f"Page PDF: {section['page']}")

        if section.get("page_document"):
            metadata.append(f"Page imprimee: {section['page_document']}")

        chunks.append(
            "\n".join(metadata)
            + "\nExtrait:\n"
            + str(section.get("text") or "")[:2_500]
        )

    return "\n\n---\n\n".join(chunks), references


def visible_terms_from_payload(payload: dict[str, Any]) -> str:
    terms = []

    for key in (
        "mti",
        "fields",
        "hsm",
        "functions",
        "errors",
        "searchable_terms",
    ):
        value = payload.get(key)

        if value:
            terms.append(json.dumps(value, ensure_ascii=False))

    return " ".join(terms)


def list_from_payload(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        return [
            {
                "field_number": key,
                "value": item,
                "namespace": "ISO8583_FIELD",
            }
            for key, item in value.items()
        ]

    return []


def field_entry_number(entry: dict[str, Any]) -> str:
    for key in ("field_number", "field", "number", "id"):
        number = normalize_field_number(entry.get(key))

        if number:
            return number

    return ""


def field_entry_value(entry: dict[str, Any]) -> str:
    for key in ("value", "data", "content", "observed_value"):
        value = entry.get(key)

        if value is not None and str(value).strip():
            return str(value).strip()

    return ""


def field_entry_namespace(entry: dict[str, Any]) -> str:
    return str(entry.get("namespace") or "ISO8583_FIELD").strip()


def observed_iso_fields(payload: dict[str, Any]) -> list[dict[str, Any]]:
    observed = []

    for item in list_from_payload(payload.get("fields")):
        if not isinstance(item, dict):
            continue

        number = field_entry_number(item)
        value = field_entry_value(item)

        if not number:
            continue

        namespace = field_entry_namespace(item)

        if namespace and namespace != "ISO8583_FIELD":
            continue

        observed.append({
            "field_number": number,
            "namespace": "ISO8583_FIELD",
            "value": value,
            "line": item.get("line") or item.get("source_line") or "",
            "confidence": item.get("confidence"),
        })

    return observed


def find_observed_iso_field(
    payload: dict[str, Any],
    field_number: str,
) -> dict[str, Any] | None:
    normalized_field = normalize_field_number(field_number)

    for field in observed_iso_fields(payload):
        if field.get("field_number") == normalized_field:
            return field

    return None


def observed_mti(payload: dict[str, Any]) -> str:
    mti = payload.get("mti")

    if isinstance(mti, dict):
        return str(mti.get("value") or mti.get("mti") or "").strip()

    return str(mti or "").strip()


def screenshot_source_reference() -> list[dict[str, Any]]:
    return [
        {
            "source": "Screenshot",
            "source_id": "SCREEN",
            "section": "Capture utilisateur",
            "page": None,
            "pdf_page": None,
            "printed_page": None,
        }
    ]


def extraction_response_from_visible_facts(
    *,
    question: str,
    visible_facts: dict[str, Any],
) -> dict[str, Any]:
    field_number = extract_requested_field_number(question)

    if question_requests_mti(question):
        mti = observed_mti(visible_facts)

        if mti:
            return {
                "summary": f"MTI : {mti}",
                "sections": [
                    {
                        "title": "Extraction",
                        "content": f"MTI visible dans la capture : {mti}.",
                        "source_ids": ["SCREEN"],
                    }
                ],
                "story": [],
                "issues": [],
                "recommendations": [],
                "references": screenshot_source_reference(),
                "evidence": [],
            }

        return {
            "summary": "Le MTI n'est pas visible ou n'a pas pu etre extrait de cette capture.",
            "sections": [],
            "story": [],
            "issues": [
                {
                    "severity": "warning",
                    "title": "MTI non extrait",
                    "detail": "La capture ne permet pas de confirmer la valeur du MTI.",
                }
            ],
            "recommendations": [],
            "references": screenshot_source_reference(),
            "evidence": [],
        }

    if not field_number:
        return {
            "summary": "Je n'ai pas identifie le champ a extraire dans la question.",
            "sections": [],
            "story": [],
            "issues": [
                {
                    "severity": "warning",
                    "title": "Champ non precise",
                    "detail": "Precise le Field ou le MTI a extraire depuis la capture.",
                }
            ],
            "recommendations": [],
            "references": screenshot_source_reference(),
            "evidence": [],
        }

    field = find_observed_iso_field(visible_facts, field_number)

    if not field or not field.get("value"):
        return {
            "summary": (
                f"Le Field {field_number} n'est pas visible ou n'a pas pu "
                "etre extrait de cette capture."
            ),
            "sections": [],
            "story": [],
            "issues": [
                {
                    "severity": "warning",
                    "title": f"Field {field_number} non extrait",
                    "detail": (
                        "Aucune valeur ISO8583_FIELD fiable n'a ete detectee "
                        "pour ce champ dans le screenshot."
                    ),
                }
            ],
            "recommendations": [],
            "references": screenshot_source_reference(),
            "evidence": [],
        }

    content = f"Field {field_number} : {field['value']}"

    if field.get("line"):
        content += f"\nLigne detectee : {field['line']}"

    return {
        "summary": f"Field {field_number} : {field['value']}",
        "sections": [
            {
                "title": "Extraction",
                "content": content,
                "source_ids": ["SCREEN"],
            }
        ],
        "story": [],
        "issues": [],
        "recommendations": [],
        "references": screenshot_source_reference(),
        "evidence": [],
    }


def payload_contains_hsm_context(payload: dict[str, Any]) -> bool:
    hsm = payload.get("hsm")

    if hsm:
        return True

    text = visible_terms_from_payload(payload).lower()
    return any(term in text for term in ("hsm", "ed01", "from hsm", "to hsm"))


def intent_requires_documentation(
    *,
    intent: str,
    question: str,
    visible_facts: dict[str, Any],
) -> bool:
    if intent == SCREEN_EXTRACTION:
        return False

    if intent == DOCUMENTATION_LOOKUP:
        return True

    normalized = normalize_query_text(question)

    if intent == SCREEN_DIAGNOSIS:
        return payload_contains_hsm_context(visible_facts) or any(
            term in normalized
            for term in ("code", "commande", "regle", "règle", "signifie")
        )

    if intent == SCREEN_VALUE_EXPLANATION:
        value = extract_mentioned_value(question)
        return bool(re.match(r"^[A-Z]{2}\d{2}$", value or ""))

    return False


def filter_references_to_used_source_ids(
    references: list[dict[str, Any]],
    source_ids: set[str],
) -> list[dict[str, Any]]:
    if not source_ids:
        return []

    return [
        reference
        for reference in references
        if str(reference.get("source_id") or "") in source_ids
    ]


def used_source_ids_from_sections(
    sections: list[dict[str, Any]],
) -> set[str]:
    source_ids: set[str] = set()

    for section in sections:
        if not isinstance(section, dict):
            continue

        values = section.get("source_ids")

        if not isinstance(values, list):
            continue

        for value in values:
            source_id = str(value or "").strip()

            if source_id:
                source_ids.add(source_id)

    return source_ids


async def extract_visible_screenshot_facts(
    *,
    question: str,
    image_documents: list[dict[str, Any]],
) -> dict[str, Any]:
    image_parts = image_content_parts(image_documents)

    if not image_parts:
        return {}

    messages = [
        {
            "role": "system",
            "content": (
                "Tu analyses uniquement le contenu visible dans des captures "
                "de traces techniques. N'utilise aucune connaissance externe. "
                "Si une valeur est illisible, marque-la comme ambigue. Retourne "
                "uniquement un JSON valide avec: summary, mti, fields, "
                "functions, hsm, errors, identifiers, log_story_sequence, "
                "ambiguities, searchable_terms. Chaque element fields doit "
                "avoir field_number, namespace='ISO8583_FIELD', value, line. "
                "Ne confonds jamais FLD (002) avec un parametre HSM."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Question utilisateur:\n{question}\n\n"
                        "Extrais les elements visibles utiles pour repondre: "
                        "MTI, Fields, fonctions, erreurs, TO HSM, FROM HSM, "
                        "codes retour, HsmResultCode et toute valeur lisible."
                    ),
                },
                *image_parts,
            ],
        },
    ]

    content = await call_hps_ai(
        messages,
        temperature=0.0,
        frequency_penalty=0.0,
        presence_penalty=0.0,
    )

    return parse_json_object(content)


def normalize_screenshot_payload(
    payload: dict[str, Any],
    references: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = str(payload.get("summary") or "").strip()

    if not summary:
        summary = "La capture a ete analysee par le modele multimodal."

    sections = payload.get("sections")

    if not isinstance(sections, list):
        sections = []

    normalized_sections = []

    for section in sections:
        if not isinstance(section, dict):
            continue

        title = str(section.get("title") or "").strip()
        content = str(section.get("content") or "").strip()

        if not title or not content:
            continue

        source_ids = section.get("source_ids")
        normalized_sections.append({
            "title": title,
            "content": content,
            "source_ids": source_ids if isinstance(source_ids, list) else [],
        })

    if not normalized_sections:
        normalized_sections = [
            {
                "title": "Analyse de la capture",
                "content": summary,
                "source_ids": [],
            }
        ]
    used_source_ids = used_source_ids_from_sections(normalized_sections)
    cited_references = filter_references_to_used_source_ids(
        references=references,
        source_ids=used_source_ids,
    )

    return {
        "summary": summary,
        "sections": normalized_sections,
        "story": [],
        "issues": [
            issue
            for issue in payload.get("issues", [])
            if isinstance(issue, dict)
        ],
        "recommendations": [
            str(item)
            for item in payload.get("recommendations", [])
            if str(item).strip()
        ],
        "references": cited_references,
        "evidence": [],
    }


async def answer_screenshot_question(
    *,
    question: str,
    conversation_id: str,
    referenced_document_ids: list[str] | None,
    agent: str,
) -> dict[str, Any] | None:
    intent = classify_screenshot_intent(question)

    if intent == DOCUMENTATION_LOOKUP:
        return None

    image_documents = await load_referenced_images(referenced_document_ids)

    if not image_documents and should_load_conversation_images(intent):
        image_documents = await load_conversation_images(
            conversation_id=conversation_id,
        )

    if not image_documents:
        return None

    try:
        visible_facts = await extract_visible_screenshot_facts(
            question=question,
            image_documents=image_documents,
        )
    except (HpsAiConfigurationError, HpsAiRequestError) as error:
        logger.info("SCREENSHOT_VISION_FACT_EXTRACTION_FAILED %s", error)
        return None

    if intent == SCREEN_EXTRACTION:
        return extraction_response_from_visible_facts(
            question=question,
            visible_facts=visible_facts,
        )

    context = ""
    references: list[dict[str, Any]] = []

    if intent_requires_documentation(
        intent=intent,
        question=question,
        visible_facts=visible_facts,
    ):
        sections = await load_context_sections(
            conversation_id=conversation_id,
            referenced_document_ids=referenced_document_ids,
            agent=agent,
        )
        retrieval_query = " ".join([
            question,
            visible_terms_from_payload(visible_facts),
        ]).strip()
        selected_sections = await select_relevant_sections(
            question=retrieval_query or question,
            sections=sections,
            limit=MAX_SCREENSHOT_CONTEXT_SECTIONS,
            use_embeddings=True,
        )
        context, references = build_context(selected_sections)

    image_parts = image_content_parts(image_documents)

    if not image_parts:
        return None

    messages = [
        {
            "role": "system",
            "content": (
                "Tu es un consultant technique senior Visa/HPS dans TRACE. "
                "Tu recois une question, une ou plusieurs captures d'ecran "
                "et eventuellement des extraits documentaires RAG. L'intention "
                "de la question est fournie. Analyse reellement l'image et "
                "separe strictement: observed=visible dans le screenshot, "
                "documented=justifie par les sources RAG, inferred=deduction "
                "technique prudente, unknown=non confirmable. Ne presente "
                "jamais une inference comme un fait certain. Ne remplace jamais "
                "une extraction demandee par une definition documentaire. Pour "
                "les captures de trace, n'utilise pas les sections generiques "
                "'Objectif du document' ou 'Organisation generale'. Utilise "
                "uniquement des sections utiles: Resume, Elements detectes, "
                "Analyse, Erreurs / anomalies, Conclusion, References. Si une "
                "signification metier vient de la documentation, cite uniquement "
                "les source_ids reellement utilises. N'invente aucune "
                "signification non visible ou non documentee. Retourne "
                "uniquement un JSON valide: "
                '{"summary": string, "sections": [{"title": string, '
                '"content": string, "source_ids": string[]}], "issues": [], '
                '"recommendations": []}.'
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Question utilisateur:\n{question}\n\n"
                        f"Intention screenshot:\n{intent}\n\n"
                        "Faits visibles extraits par lecture image "
                        "(source observed, a verifier sur la capture, ne pas "
                        "les traiter comme source documentaire):\n"
                        f"{json.dumps(visible_facts, ensure_ascii=False)}\n\n"
                        "Extraits documentaires RAG disponibles:\n"
                        f"{context or 'Aucun extrait documentaire pertinent disponible.'}\n\n"
                        "Consigne: reponds en francais si la question est en "
                        "francais. Explique la sequence visible et distingue "
                        "ce qui est visible dans l'image de ce qui est justifie "
                        "par la documentation."
                    ),
                },
                *image_parts,
            ],
        },
    ]

    try:
        content = await call_hps_ai(
            messages,
            temperature=0.1,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )
    except (HpsAiConfigurationError, HpsAiRequestError) as error:
        logger.info("SCREENSHOT_VISION_ANSWER_FAILED %s", error)
        return None

    payload = parse_json_object(content)

    if not payload:
        payload = {
            "summary": content,
            "sections": [
                {
                    "title": "Analyse de la capture",
                    "content": content,
                    "source_ids": [],
                }
            ],
            "issues": [],
            "recommendations": [],
        }

    return normalize_screenshot_payload(
        payload=payload,
        references=references,
    )
