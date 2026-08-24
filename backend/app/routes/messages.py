import json
from datetime import datetime, timezone
from typing import Any, Literal

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pydantic import BaseModel, Field


from app.database import conversations_collection, messages_collection


router = APIRouter(
    prefix="/api/conversations",
    tags=["Messages"],
)


class MessageCreate(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""
    structured: dict[str, Any] | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)




def serialize_message(document: dict) -> dict:
    structured = (
        document.get("structured")
        or document.get("structured_response")
        or document.get("answer")
    )
    if isinstance(structured, str):
        try:
            parsed_structured = json.loads(structured)
            structured = parsed_structured if isinstance(parsed_structured, dict) else None
        except json.JSONDecodeError:
            structured = None

    return {
        "id": str(document["_id"]),
        "conversation_id": document["conversation_id"],
        "role": document["role"],
        "content": document.get("content", ""),
        "structured": structured,
        "created_at": document["created_at"],
        "attachments": document.get("attachments", []),
    }


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: str):
    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid conversation ID",
        )

    cursor = messages_collection.find(
        {"conversation_id": conversation_id}
    ).sort("created_at", 1)

    documents = await cursor.to_list(length=500)

    return [
        serialize_message(document)
        for document in documents
    ]


@router.post("/{conversation_id}/messages", status_code=201)
async def create_message(
    conversation_id: str,
    payload: MessageCreate,
):
    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid conversation ID",
        )

    conversation = await conversations_collection.find_one(
        {"_id": ObjectId(conversation_id)}
    )

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    now = datetime.now(timezone.utc)


    document = {
    "conversation_id": conversation_id,
    "role": payload.role,
    "content": payload.content,
    "structured": payload.structured,
    "attachments": payload.attachments,
    "created_at": now,
    }

    result = await messages_collection.insert_one(document)
    document["_id"] = result.inserted_id

    await conversations_collection.update_one(
        {"_id": ObjectId(conversation_id)},
        {"$set": {"updated_at": now}},
    )

    return serialize_message(document)
