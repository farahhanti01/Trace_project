from typing import List, Literal

from bson import ObjectId
from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from app.database import conversations_collection
from app.services.document_service import (
    list_documents,
    list_documents_by_conversation,
    # reindex_document,
    save_document,
)


router = APIRouter(
    prefix="/api/documents",
    tags=["Documents"],
)


AgentType = Literal[
    "documentation",
    "log",
]


async def verify_conversation(
    conversation_id: str,
) -> None:
    """
    Verify that the conversation ID is valid
    and that the conversation exists.
    """

    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid conversation ID.",
        )

    conversation = await conversations_collection.find_one(
        {
            "_id": ObjectId(conversation_id),
        }
    )

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )


@router.post(
    "/upload",
    status_code=201,
)
async def upload_documents(
    conversation_id: str = Form(...),
    agent: AgentType = Form(...),
    files: List[UploadFile] = File(...),
):
    """
    Upload one or multiple documents.

    The files are saved physically on disk,
    while their metadata is stored in MongoDB.
    """

    await verify_conversation(conversation_id)

    if not files:
        raise HTTPException(
            status_code=400,
            detail="No files were provided.",
        )

    saved_documents = []

    for upload_file in files:
        document = await save_document(
            upload_file=upload_file,
            conversation_id=conversation_id,
            agent=agent,
        )

        saved_documents.append(document)

    return {
        "message": "Documents uploaded successfully.",
        "conversation_id": conversation_id,
        "documents": saved_documents,
    }


@router.get("")
async def get_documents(
    agent: AgentType | None = None,
    status: str | None = None,
):
    """
    Return documents across conversations.
    Used by the composer to mention reference docs with #.
    """

    return await list_documents(
        agent=agent,
        status=status,
    )


# @router.post("/{document_id}/reindex")
# async def reindex_existing_document(
#     document_id: str,
# ):
#     """
#     Re-extract a stored file and rebuild its RAG chunks.
#     Embeddings are generated when the configured provider supports them.
#     """
#
#     return await reindex_document(document_id)


@router.get(
    "/conversation/{conversation_id}",
)
async def get_conversation_documents(
    conversation_id: str,
):
    """
    Return all documents associated
    with one conversation.
    """

    await verify_conversation(conversation_id)

    return await list_documents_by_conversation(
        conversation_id
    )
