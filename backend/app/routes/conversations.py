from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import (
    conversation_memory_states_collection,
    conversations_collection,
    messages_collection,
)


router = APIRouter(
    prefix="/api/conversations",
    tags=["Conversations"],
)


def serialize_conversation(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "title": document["title"],
        "agent": document.get("agent", "documentation"),
        "pinned": bool(document.get("pinned", False)),
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }


class ConversationCreate(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=100,
    )
    agent: str = "documentation"


class ConversationUpdate(BaseModel):
    pinned: bool | None = None


@router.get("")
async def list_conversations():
    cursor = conversations_collection.find().sort(
        [
            ("pinned", -1),
            ("updated_at", -1),
        ],
    )

    documents = await cursor.to_list(length=100)

    return [
        serialize_conversation(document)
        for document in documents
    ]


@router.post("", status_code=201)
async def create_conversation(
    payload: ConversationCreate,
):
    now = datetime.now(timezone.utc)

    document = {
        "title": payload.title,
        "agent": payload.agent,
        "created_at": now,
        "updated_at": now,
    }

    result = await conversations_collection.insert_one(
        document
    )

    document["_id"] = result.inserted_id

    return serialize_conversation(document)


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
):
    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid conversation ID",
        )

    update_fields = {}

    if payload.pinned is not None:
        update_fields["pinned"] = payload.pinned

    if not update_fields:
        raise HTTPException(
            status_code=400,
            detail="No update fields provided",
        )

    object_id = ObjectId(conversation_id)

    result = await conversations_collection.update_one(
        {"_id": object_id},
        {"$set": update_fields},
    )

    if result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    document = await conversations_collection.find_one(
        {"_id": object_id}
    )

    return serialize_conversation(document)


@router.delete(
    "/{conversation_id}",
    status_code=204,
)
async def delete_conversation(
    conversation_id: str,
):
    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid conversation ID",
        )

    object_id = ObjectId(conversation_id)

    result = await conversations_collection.delete_one(
        {"_id": object_id}
    )

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    await messages_collection.delete_many(
        {
            "conversation_id": conversation_id,
        }
    )

    await conversation_memory_states_collection.delete_one(
        {
            "conversation_id": conversation_id,
        }
    )

    
