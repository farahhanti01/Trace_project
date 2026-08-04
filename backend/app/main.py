from typing import Any, Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.routes.conversations import router as conversations_router
from app.routes.messages import router as messages_router
from app.routes.documents import router as documents_router
from app.routes.admin import router as admin_router
from app.services.documentation_agent_service import (
    answer_documentation_question,
)
from app.services.log_analysis_agent_service import (
    answer_log_question,
)
from app.services.chat_intent_router import classify_chat_workflow

app = FastAPI(
    title="TRACE AI API",
    version="1.0.0",
    description="Backend API for the TRACE AI Platform.",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Register API routers
app.include_router(conversations_router)
app.include_router(messages_router)
app.include_router(documents_router)
app.include_router(admin_router)

AgentType = Literal["documentation", "log"]


class Reference(BaseModel):
    source: str
    original_source: str | None = None
    page: int | None = None
    pdf_page: int | None = None
    printed_page: str | None = None
    source_id: str | None = None
    section: str | None = None
    sheet: str | None = None
    paragraph: int | None = None


class Evidence(Reference):
    heading: str | None = None
    excerpt: str
    score: float | None = None


class Issue(BaseModel):
    severity: Literal["info", "warning", "error"]
    title: str
    detail: str | None = None


class ResponseSectionItem(BaseModel):
    label: str
    content: str


class TableColumn(BaseModel):
    key: str
    label: str


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    content: str


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    style: Literal["bullet", "numbered"] = "bullet"
    items: list[str]


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    title: str | None = None
    columns: list[TableColumn]
    rows: list[dict[str, str]]


class CodeBlock(BaseModel):
    type: Literal["code"] = "code"
    language: str = "text"
    content: str


class KeyValueItem(BaseModel):
    label: str
    value: str


class KeyValueBlock(BaseModel):
    type: Literal["key_value"] = "key_value"
    items: list[KeyValueItem]


class CalloutBlock(BaseModel):
    type: Literal["callout"] = "callout"
    severity: Literal["info", "warning", "error", "success"] = "info"
    title: str | None = None
    content: str


ResponseBlock = (
    ParagraphBlock
    | ListBlock
    | TableBlock
    | CodeBlock
    | KeyValueBlock
    | CalloutBlock
)


class ResponseSection(BaseModel):
    title: str
    content: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    items: list[ResponseSectionItem] = Field(default_factory=list)
    blocks: list[ResponseBlock] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class StructuredResponse(BaseModel):
    summary: str
    sections: list[ResponseSection] = Field(default_factory=list)
    story: list[Any] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    transactions: list[dict[str, Any]] = Field(default_factory=list)
    statistics: dict[str, int] = Field(default_factory=dict)
    display_options: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    agent: AgentType
    conversation_id: str | None = None
    referenced_document_ids: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    conversation_id: str | None = None
    agent: AgentType
    answer: StructuredResponse


@app.get("/")
def root():
    return {
        "message": "TRACE AI backend is running",
        "status": "ok",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    workflow = classify_chat_workflow(
        question=request.question,
        selected_agent=request.agent,
    )

    if workflow == "LOG_COMPLIANCE_ANALYSIS":
        result = StructuredResponse(
            **await answer_log_question(
                question=request.question,
                conversation_id=request.conversation_id,
                referenced_document_ids=request.referenced_document_ids,
            )
        )

    else:
        result = StructuredResponse(
            **await answer_documentation_question(
                question=request.question,
                conversation_id=request.conversation_id,
                referenced_document_ids=request.referenced_document_ids,
                include_all_agents=request.agent == "log",
            )
        )

    return ChatResponse(
        conversation_id=request.conversation_id,
        agent=request.agent,
        answer=result,
    )


# @app.post("/api/files")
# async def upload_files(
#     agent: AgentType = Form(...),
#     files: list[UploadFile] = File(...),
# ):
#     allowed_extensions = {
#         ".pdf",
#         ".doc",
#         ".docx",
#         ".xls",
#         ".xlsx",
#         ".txt",
#         ".log",
#     }

#     uploaded_files = []

#     for file in files:
#         filename = file.filename or ""

#         if not filename:
#             raise HTTPException(
#                 status_code=400,
#                 detail="A file does not have a valid filename.",
#             )

#         if "." not in filename:
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"File extension missing for {filename}.",
#             )

#         extension = "." + filename.rsplit(".", 1)[-1].lower()

#         if extension not in allowed_extensions:
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"Unsupported file type: {extension}",
#             )

#         content = await file.read()

#         uploaded_files.append(
#             {
#                 "filename": filename,
#                 "content_type": file.content_type,
#                 "size": len(content),
#             }
#         )

#     return {
#         "message": "Files received successfully",
#         "agent": agent,
#         "files": uploaded_files,
#     }
