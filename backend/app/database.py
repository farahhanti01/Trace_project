import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")
MONGODB_DB = os.getenv("MONGODB_DB", "trace_ai")

if not MONGODB_URL:
    raise RuntimeError("MONGODB_URL is not configured")


client = AsyncIOMotorClient(MONGODB_URL)
database = client[MONGODB_DB]

conversations_collection = database["conversations"]
messages_collection = database["messages"]
documents_collection = database["documents"]
document_sections_collection = database["document_sections"]
document_facts_collection = database["document_facts"]
document_content_units_collection = database["document_content_units"]
function_catalog_collection = database["function_catalog"]
conversation_memory_states_collection = database["conversation_memory_state"]


async def ensure_document_content_unit_indexes() -> None:
    """Cree les indexes minimaux pour les unites documentaires generiques."""

    await document_content_units_collection.create_index("document_id")
    await document_content_units_collection.create_index("conversation_id")
    await document_content_units_collection.create_index("content_type")
    await document_content_units_collection.create_index("entities.field_number")
    await document_content_units_collection.create_index("entities.code")
    await document_content_units_collection.create_index("hierarchy.chapter")
    await document_content_units_collection.create_index("hierarchy.section")
    await document_content_units_collection.create_index("source_location.pdf_page")
    await document_content_units_collection.create_index([
        ("document_id", 1),
        ("content_type", 1),
    ])
    await document_content_units_collection.create_index([
        ("document_id", 1),
        ("entities.field_number", 1),
    ])

    await function_catalog_collection.create_index("document_id")
    await function_catalog_collection.create_index("conversation_id")
    await function_catalog_collection.create_index("function_name")
    await function_catalog_collection.create_index("normalized_function_name")
    await function_catalog_collection.create_index([
        ("conversation_id", 1),
        ("normalized_function_name", 1),
    ])
    await function_catalog_collection.create_index([
        ("document_id", 1),
        ("normalized_function_name", 1),
    ])

    await conversation_memory_states_collection.create_index(
        "conversation_id",
        unique=True,
    )
