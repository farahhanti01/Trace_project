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