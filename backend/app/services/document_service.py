from datetime import datetime, timezone
from pathlib import Path
import hashlib
import logging
import re

import aiofiles
from bson import ObjectId
from fastapi import HTTPException, UploadFile

from app.database import (
    document_content_units_collection,
    document_facts_collection,
    document_sections_collection,
    documents_collection,
    function_catalog_collection,
)

from app.services.extraction_service import (
    DocumentExtractionError,
    UnsupportedDocumentTypeError,
    extract_document,
    mask_iso_field_002,
)
from app.services.file_type_service import (
    ALLOWED_DOCUMENT_EXTENSIONS,
    is_supported_document_extension,
    is_trace_extension,
)
from app.services.hps_ai_service import (
    HpsAiConfigurationError,
    HpsAiRequestError,
    create_hps_embeddings,
)
from app.services.document_fact_service import (
    extract_document_facts,
)
from app.services.content_unit_service import (
    ContentUnitExtractor,
)
from app.services.function_catalog_service import (
    FunctionCatalogExtractor,
)


ALLOWED_EXTENSIONS = ALLOWED_DOCUMENT_EXTENSIONS

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_CHUNK_CHARACTERS = 1_800
CHUNK_OVERLAP_CHARACTERS = 250
EMBEDDING_BATCH_SIZE = 32

FIELD_TITLE_PATTERN = r"\bField\s+0*(?P<number>\d{1,3}(?:\.\d+)?)\s*[—-]\s*(?P<name>[A-Za-z0-9 /()_.-]+)"

# backend/
BACKEND_ROOT = Path(__file__).resolve().parents[2]

# backend/storage/documents/
DOCUMENT_STORAGE_ROOT = (
    BACKEND_ROOT / "storage" / "documents"
)

DOCUMENT_STORAGE_ROOT.mkdir(
    parents=True,
    exist_ok=True,
)

logger = logging.getLogger(__name__)


def split_text_into_chunks(
    text: str,
    max_characters: int = MAX_CHUNK_CHARACTERS,
    overlap_characters: int = CHUNK_OVERLAP_CHARACTERS,
) -> list[str]:
    cleaned_text = text.strip()

    if not cleaned_text:
        return []

    if len(cleaned_text) <= max_characters:
        return [cleaned_text]

    chunks = []
    current_units = []
    current_size = 0

    for unit in split_text_units(cleaned_text):
        unit_size = len(unit)

        if unit_size > max_characters:
            if current_units:
                chunks.append("\n".join(current_units).strip())
                current_units = []
                current_size = 0

            chunks.extend(
                split_large_unit(
                    unit,
                    max_characters=max_characters,
                    overlap_characters=overlap_characters,
                )
            )
            continue

        separator_size = 1 if current_units else 0

        if (
            current_units
            and current_size + separator_size + unit_size > max_characters
        ):
            chunks.append("\n".join(current_units).strip())
            current_units = overlap_units(
                current_units,
                max_characters=overlap_characters,
            )
            current_size = len("\n".join(current_units))

        current_units.append(unit)
        current_size += separator_size + unit_size

    if current_units:
        chunks.append("\n".join(current_units).strip())

    return chunks


def split_text_units(
    text: str,
) -> list[str]:
    """
    Split text into semantic-ish units without assuming a document domain.
    Paragraphs, table rows and technical lines remain independent units.
    """

    paragraphs = [
        paragraph.strip()
        for paragraph in text.replace("\r\n", "\n").split("\n\n")
        if paragraph.strip()
    ]

    units = []

    for paragraph in paragraphs:
        lines = [
            line.strip()
            for line in paragraph.splitlines()
            if line.strip()
        ]

        if not lines:
            continue

        if len(lines) > 1 and looks_like_structured_block(lines):
            units.extend(lines)
        else:
            units.append(" ".join(lines))

    return units


def looks_like_structured_block(
    lines: list[str],
) -> bool:
    structured_lines = 0

    for line in lines:
        if (
            "|" in line
            or "\t" in line
            or line.startswith(("-", "*"))
            or line[:2].isdigit()
            or ":" in line[:80]
        ):
            structured_lines += 1

    return structured_lines >= max(2, len(lines) // 2)


def overlap_units(
    units: list[str],
    max_characters: int,
) -> list[str]:
    overlapped = []
    size = 0

    for unit in reversed(units):
        unit_size = len(unit)

        if overlapped and size + unit_size > max_characters:
            break

        overlapped.insert(0, unit)
        size += unit_size + 1

    return overlapped


def split_large_unit(
    unit: str,
    max_characters: int,
    overlap_characters: int,
) -> list[str]:
    chunks = []
    start = 0

    while start < len(unit):
        end = min(start + max_characters, len(unit))

        if end < len(unit):
            split_at = unit.rfind(" ", start, end)

            if split_at > start:
                end = split_at

        chunk = unit[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(unit):
            break

        start = max(end - overlap_characters, start + 1)

    return chunks


def normalize_field_number(value: str) -> str:
    if "." in value:
        left, right = value.split(".", 1)
        return f"{int(left):03d}.{right}" if left.isdigit() else value

    return f"{int(value):03d}" if value.isdigit() else value


def extract_field_metadata(
    text: str,
    heading: str | None,
) -> dict:
    combined_text = f"{heading or ''}\n{text}"
    field_match = re.search(
        FIELD_TITLE_PATTERN,
        combined_text,
        flags=re.IGNORECASE,
    )

    if not field_match:
        field_match = re.search(
            r"\b(?:Field|FLD)\s*\(?0*(?P<number>\d{1,3}(?:\.\d+)?)\)?",
            combined_text,
            flags=re.IGNORECASE,
        )

    if not field_match:
        return {}

    field_number = normalize_field_number(field_match.group("number"))
    field_name = (
        field_match.groupdict().get("name", "").strip(" .:-")
        if "name" in field_match.groupdict()
        else ""
    )
    lowered_text = combined_text.lower()

    content_type = "field_related"

    if "attribute" in lowered_text:
        content_type = "field_attributes"
    elif "description" in lowered_text:
        content_type = "field_definition"
    elif "usage" in lowered_text:
        content_type = "field_usage"
    elif "field edits" in lowered_text or "reject" in lowered_text:
        content_type = "field_edits"
    elif "values" in lowered_text or "codes" in lowered_text:
        content_type = "field_values"

    page_document_matches = re.findall(
        r"\b(\d{1,2}-\d{1,3})\s+(?:Visa Confidential|BASE I Technical)",
        combined_text,
        flags=re.IGNORECASE,
    )

    if not page_document_matches:
        page_document_matches = re.findall(
            r"(?:Visa Confidential|BASE I Technical)[^\n]{0,120}\b(\d{1,2}-\d{1,3})\b",
            combined_text,
            flags=re.IGNORECASE,
        )

    metadata = {
        "field_number": field_number,
        "content_type": content_type,
    }

    if field_name:
        metadata["field_name"] = field_name

    if page_document_matches:
        metadata["page_document"] = page_document_matches[-1]

    return metadata


def sha256_hex(
    content: bytes,
) -> str:
    """Calcule une empreinte stable pour reconnaitre un fichier deja uploade."""

    return hashlib.sha256(content).hexdigest()


def duplicate_document_query(
    *,
    conversation_id: str,
    agent: str,
    extension: str,
    file_hash: str,
) -> dict:
    """Construit la requete MongoDB utilisee pour detecter un doublon."""

    return {
        "conversation_id": conversation_id,
        "agent": agent,
        "extension": extension,
        "file_hash": file_hash,
        "status": {
            "$in": [
                "uploaded",
                "extracting",
                "extracted",
                "extraction_pending",
            ],
        },
    }


async def add_embeddings_to_sections(
    section_documents: list[dict],
) -> str:
    if not section_documents:
        return "skipped"

    try:
        for start in range(
            0,
            len(section_documents),
            EMBEDDING_BATCH_SIZE,
        ):
            batch = section_documents[
                start:start + EMBEDDING_BATCH_SIZE
            ]

            embeddings = await create_hps_embeddings(
                [
                    section["text"]
                    for section in batch
                ]
            )

            for section, embedding in zip(batch, embeddings):
                section["embedding"] = embedding
                section["embedding_model"] = "configured"

    except (HpsAiConfigurationError, HpsAiRequestError):
        return "failed"

    return "embedded"


async def store_content_units_best_effort(
    *,
    document_id: ObjectId,
    section_documents: list[dict],
    document: dict | None,
    fallback_filename: str,
) -> int:
    """Stocke les ContentUnits sans bloquer l'ingestion principale."""

    try:
        await document_content_units_collection.delete_many(
            {
                "document_id": str(document_id),
            }
        )
        content_units = ContentUnitExtractor.extract(
            sections=section_documents,
            document=document or {
                "_id": document_id,
                "original_filename": fallback_filename,
            },
        )

        if content_units:
            await document_content_units_collection.insert_many(
                content_units
            )

        return len(content_units)

    except Exception as error:
        await document_content_units_collection.delete_many(
            {
                "document_id": str(document_id),
            }
        )
        logger.exception(
            "ContentUnit extraction failed for document %s: %s",
            document_id,
            error,
        )

        return 0


async def store_function_catalog_best_effort(
    *,
    document_id: ObjectId,
    section_documents: list[dict],
    document: dict | None,
    fallback_filename: str,
) -> int:
    """Stocke un catalogue de fonctions sans bloquer l'ingestion principale."""

    try:
        await function_catalog_collection.delete_many(
            {
                "document_id": str(document_id),
            }
        )
        entries = FunctionCatalogExtractor.extract(
            sections=section_documents,
            document=document or {
                "_id": document_id,
                "original_filename": fallback_filename,
            },
        )

        if entries:
            await function_catalog_collection.insert_many(entries)

        return len(entries)

    except Exception as error:
        await function_catalog_collection.delete_many(
            {
                "document_id": str(document_id),
            }
        )
        logger.exception(
            "Function catalog extraction failed for document %s: %s",
            document_id,
            error,
        )

        return 0


def serialize_document(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "conversation_id": document["conversation_id"],
        "original_filename": document["original_filename"],
        "stored_filename": document["stored_filename"],
        "relative_path": document["relative_path"],
        "extension": document["extension"],
        "content_type": document.get("content_type"),
        "size": document["size"],
        "agent": document["agent"],
        "status": document["status"],
        "encoding": document.get("encoding"),
        "content_length": document.get(
            "content_length",
            0,
        ),
        "line_count": document.get(
            "line_count",
            0,
        ),
        "section_count": document.get(
            "section_count",
            0,
        ),
        "embedding_status": document.get(
            "embedding_status",
            "unknown",
        ),
        "file_hash": document.get("file_hash"),
        "deduplicated": document.get("deduplicated", False),
        "extraction_error": document.get(
            "extraction_error"
        ),
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
        "extracted_at": document.get(
            "extracted_at"
        ),
    }


async def save_file_to_disk(
    upload_file: UploadFile,
    destination: Path,
) -> tuple[int, str]:
    """
    Save an uploaded file progressively.

    Returns the total file size in bytes.
    """

    total_size = 0
    digest = hashlib.sha256()

    try:
        async with aiofiles.open(
            destination,
            "wb",
        ) as output:
            while True:
                chunk = await upload_file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File '{upload_file.filename}' "
                            "exceeds the 20 MB limit."
                        ),
                    )

                digest.update(chunk)
                await output.write(chunk)

    except Exception:
        if destination.exists():
            destination.unlink()

        raise

    finally:
        await upload_file.close()

    return total_size, digest.hexdigest()


async def save_text_file_to_disk_masked(
    upload_file: UploadFile,
    destination: Path,
) -> tuple[int, str]:
    content = await upload_file.read()

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    masked_text = mask_iso_field_002(text)
    encoded = masked_text.encode("utf-8")
    file_hash = sha256_hex(encoded)

    if len(encoded) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File '{upload_file.filename}' "
                "exceeds the 20 MB limit."
            ),
        )

    try:
        async with aiofiles.open(destination, "wb") as output:
            await output.write(encoded)
    except Exception:
        if destination.exists():
            destination.unlink()

        raise
    finally:
        await upload_file.close()

    return len(encoded), file_hash


async def extract_and_store_document(
    document_id: ObjectId,
    file_path: Path,
) -> None:
    """
    Extract the document content and save its technical
    sections in MongoDB.
    """

    now = datetime.now(timezone.utc)
    document = await documents_collection.find_one(
        {
            "_id": document_id,
        }
    )

    await documents_collection.update_one(
        {
            "_id": document_id,
        },
        {
            "$set": {
                "status": "extracting",
                "updated_at": now,
            }
        },
    )

    try:
        extraction_result = extract_document(
            file_path
        )

        # Avoid duplicate sections if extraction is retried.
        await document_sections_collection.delete_many(
            {
                "document_id": str(document_id),
            }
        )
        await document_content_units_collection.delete_many(
            {
                "document_id": str(document_id),
            }
        )
        await document_facts_collection.delete_many(
            {
                "document_id": str(document_id),
            }
        )
        await function_catalog_collection.delete_many(
            {
                "document_id": str(document_id),
            }
        )

        section_documents = []

        for index, section in enumerate(
            extraction_result["sections"]
        ):
            if isinstance(section, dict):
                section_text = section.get("text", "")
                page = section.get("page")
                sheet = section.get("sheet")
                paragraph = section.get("paragraph")
                heading = section.get("heading")
                section_metadata = {
                    key: section.get(key)
                    for key in (
                        "field_number",
                        "field_name",
                        "content_type",
                        "page_document",
                        "cells",
                        "row_number",
                    )
                    if section.get(key) is not None
                }
            else:
                section_text = section
                page = None
                sheet = None
                paragraph = index + 1
                heading = None
                section_metadata = {}

            if not section_text:
                continue

            section_metadata = {
                **extract_field_metadata(
                    text=section_text,
                    heading=heading,
                ),
                **section_metadata,
            }

            chunks = split_text_into_chunks(
                section_text
            )

            for chunk_index, chunk_text in enumerate(chunks):
                section_documents.append(
                    {
                        "document_id": str(document_id),
                        "section_index": index,
                        "chunk_index": chunk_index,
                        "text": chunk_text,
                        "character_count": len(
                            chunk_text
                        ),
                        "source_character_count": len(
                            section_text
                        ),
                        "page": page,
                        "sheet": sheet,
                        "paragraph": paragraph,
                        "heading": heading,
                        **section_metadata,
                        "created_at": now,
                    }
                )

        if section_documents:
            embedding_status = await add_embeddings_to_sections(
                section_documents
            )

            await document_sections_collection.insert_many(
                section_documents
            )
            await store_content_units_best_effort(
                document_id=document_id,
                section_documents=section_documents,
                document=document,
                fallback_filename=file_path.name,
            )
            function_catalog_count = (
                0
                if is_trace_extension(file_path.suffix.lower())
                else await store_function_catalog_best_effort(
                    document_id=document_id,
                    section_documents=section_documents,
                    document=document,
                    fallback_filename=file_path.name,
                )
            )
        else:
            embedding_status = "skipped"
            function_catalog_count = 0

        structured_facts = extract_document_facts(
            sections=section_documents,
            document=document or {
                "_id": document_id,
                "original_filename": file_path.name,
            },
        )

        if structured_facts:
            await document_facts_collection.insert_many(
                structured_facts
            )

        await documents_collection.update_one(
            {
                "_id": document_id,
            },
            {
                "$set": {
                    "status": "extracted",
                    "encoding": extraction_result.get(
                        "encoding"
                    ),
                    "content_length": extraction_result.get(
                        "content_length",
                        0,
                    ),
                    "line_count": extraction_result.get(
                        "line_count",
                        0,
                    ),
                    "section_count": len(
                        section_documents
                    ),
                    "fact_count": len(
                        structured_facts
                    ),
                    "function_catalog_count": function_catalog_count,
                    "embedding_status": embedding_status,
                    "extracted_at": now,
                    "updated_at": now,
                },
                "$unset": {
                    "extraction_error": "",
                },
            },
        )

    except UnsupportedDocumentTypeError as error:
        await documents_collection.update_one(
            {
                "_id": document_id,
            },
            {
                "$set": {
                    "status": "extraction_pending",
                    "extraction_error": str(error),
                    "updated_at": now,
                }
            },
        )

    except (
        DocumentExtractionError,
        OSError,
        ValueError,
    ) as error:
        await documents_collection.update_one(
            {
                "_id": document_id,
            },
            {
                "$set": {
                    "status": "extraction_failed",
                    "extraction_error": str(error),
                    "updated_at": now,
                }
            },
        )


async def save_document(
    upload_file: UploadFile,
    conversation_id: str,
    agent: str,
) -> dict:
    original_filename = Path(
        upload_file.filename or ""
    ).name

    if not original_filename:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file has no valid filename.",
        )

    extension = Path(
        original_filename
    ).suffix.lower()

    if not is_supported_document_extension(extension):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file extension: "
                f"{extension or 'unknown'}"
            ),
        )

    document_object_id = ObjectId()
    document_id = str(document_object_id)

    conversation_folder = (
        DOCUMENT_STORAGE_ROOT / conversation_id
    )

    conversation_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    stored_filename = (
        f"{document_id}{extension}"
    )

    destination = (
        conversation_folder / stored_filename
    )

    if is_trace_extension(extension):
        file_size, file_hash = await save_text_file_to_disk_masked(
            upload_file=upload_file,
            destination=destination,
        )
    else:
        file_size, file_hash = await save_file_to_disk(
            upload_file=upload_file,
            destination=destination,
        )

    existing_document = await documents_collection.find_one(
        duplicate_document_query(
            conversation_id=conversation_id,
            agent=agent,
            extension=extension,
            file_hash=file_hash,
        )
    )

    if existing_document:
        if destination.exists():
            destination.unlink()

        if (
            extension == ".pdf"
            and existing_document.get("fact_count") is None
            and existing_document.get("relative_path")
        ):
            existing_path = BACKEND_ROOT / existing_document["relative_path"]

            if existing_path.exists():
                await extract_and_store_document(
                    document_id=existing_document["_id"],
                    file_path=existing_path,
                )
                existing_document = (
                    await documents_collection.find_one(
                        {
                            "_id": existing_document["_id"],
                        }
                    )
                    or existing_document
                )

        return {
            **serialize_document(existing_document),
            "deduplicated": True,
        }

    relative_path = destination.relative_to(
        BACKEND_ROOT
    )

    now = datetime.now(timezone.utc)

    document = {
        "_id": document_object_id,
        "conversation_id": conversation_id,
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "relative_path": str(relative_path),
        "extension": extension,
        "content_type": upload_file.content_type,
        "size": file_size,
        "file_hash": file_hash,
        "agent": agent,
        "status": "uploaded",
        "created_at": now,
        "updated_at": now,
    }

    try:
        await documents_collection.insert_one(
            document
        )

        await extract_and_store_document(
            document_id=document_object_id,
            file_path=destination,
        )

        updated_document = (
            await documents_collection.find_one(
                {
                    "_id": document_object_id,
                }
            )
        )

        return serialize_document(
            updated_document or document
        )

    except HTTPException:
        raise

    except Exception as error:
        if destination.exists():
            destination.unlink()

        await documents_collection.delete_one(
            {
                "_id": document_object_id,
            }
        )

        await document_sections_collection.delete_many(
            {
                "document_id": document_id,
            }
        )
        await document_content_units_collection.delete_many(
            {
                "document_id": document_id,
            }
        )
        await document_facts_collection.delete_many(
            {
                "document_id": document_id,
            }
        )
        await function_catalog_collection.delete_many(
            {
                "document_id": document_id,
            }
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to save document "
                f"'{original_filename}'."
            ),
        ) from error


async def list_documents_by_conversation(
    conversation_id: str,
) -> list[dict]:
    cursor = documents_collection.find(
        {
            "conversation_id": conversation_id,
        }
    ).sort(
        "created_at",
        -1,
    )

    documents = await cursor.to_list(
        length=200
    )

    return [
        serialize_document(document)
        for document in documents
    ]


async def list_documents(
    agent: str | None = None,
    status: str | None = None,
) -> list[dict]:
    query = {}

    if agent:
        query["agent"] = agent

    if status:
        query["status"] = status

    cursor = documents_collection.find(query).sort(
        "created_at",
        -1,
    )

    documents = await cursor.to_list(
        length=500
    )

    return [
        serialize_document(document)
        for document in documents
    ]


async def get_document(
    document_id: str,
) -> dict | None:
    if not ObjectId.is_valid(document_id):
        return None

    document = await documents_collection.find_one(
        {
            "_id": ObjectId(document_id),
        }
    )

    if document is None:
        return None

    return serialize_document(document)


async def reindex_document(
    document_id: str,
) -> dict:
    if not ObjectId.is_valid(document_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid document ID.",
        )

    object_id = ObjectId(document_id)
    document = await documents_collection.find_one(
        {
            "_id": object_id,
        }
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    file_path = BACKEND_ROOT / document["relative_path"]

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Stored file not found on disk.",
        )

    await extract_and_store_document(
        document_id=object_id,
        file_path=file_path,
    )

    updated_document = await documents_collection.find_one(
        {
            "_id": object_id,
        }
    )

    return serialize_document(
        updated_document or document
    )
