from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.routes.conversations import router as conversations_router
from app.routes.messages import router as messages_router


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


AgentType = Literal["documentation", "log"]


class Reference(BaseModel):
    source: str
    page: int | None = None


class Issue(BaseModel):
    severity: Literal["info", "warning", "error"]
    title: str
    detail: str | None = None


class StructuredResponse(BaseModel):
    summary: str
    story: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    agent: AgentType
    conversation_id: str | None = None


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
def chat(request: ChatRequest):
    if request.agent == "log":
        result = StructuredResponse(
            summary=(
                "Analyzed 1,284 log entries from the provided authorization "
                "trace. Identified a partial reversal path and one "
                "non-conformity against the ISO 8583 specification."
            ),
            story=[
                "Terminal 74210 initiated a purchase authorization (MTI 0100).",
                "The acquirer switch routed the request to the Visa network.",
                "The issuer approved the request with response code 00.",
                "A timeout triggered an automatic reversal message.",
            ],
            issues=[
                Issue(
                    severity="error",
                    title="Missing DE-39 in reversal message",
                    detail=(
                        "The reversal message is missing the response code "
                        "from the original approved authorization."
                    ),
                ),
                Issue(
                    severity="warning",
                    title="Response latency above threshold",
                    detail="Issuer round-trip time exceeded the expected SLA.",
                ),
            ],
            recommendations=[
                "Backfill DE-39 during reversal generation.",
                "Investigate latency between the acquirer and issuer.",
            ],
            references=[
                Reference(source="ISO8583-1987.pdf", page=42),
                Reference(source="SwitchValidation.docx", page=12),
            ],
        )

    else:
        result = StructuredResponse(
            summary=(
                f'Reviewed the available documentation to answer: '
                f'"{request.question}".'
            ),
            story=[
                "Located the relevant documentation section.",
                "Extracted the applicable business rules.",
                "Cross-referenced the authorization requirements.",
            ],
            issues=[
                Issue(
                    severity="info",
                    title="Potential wording ambiguity",
                    detail=(
                        "The specification may allow more than one "
                        "interpretation."
                    ),
                ),
            ],
            recommendations=[
                "Confirm the interpretation with the specification owner.",
                "Add a test case covering the identified scenario.",
            ],
            references=[
                Reference(source="Visa Specification.pdf", page=42),
                Reference(source="Authorization.docx", page=7),
            ],
        )

    return ChatResponse(
        conversation_id=request.conversation_id,
        agent=request.agent,
        answer=result,
    )


@app.post("/api/files")
async def upload_files(
    agent: AgentType = Form(...),
    files: list[UploadFile] = File(...),
):
    allowed_extensions = {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".txt",
        ".log",
    }

    uploaded_files = []

    for file in files:
        filename = file.filename or ""

        if not filename:
            raise HTTPException(
                status_code=400,
                detail="A file does not have a valid filename.",
            )

        if "." not in filename:
            raise HTTPException(
                status_code=400,
                detail=f"File extension missing for {filename}.",
            )

        extension = "." + filename.rsplit(".", 1)[-1].lower()

        if extension not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {extension}",
            )

        content = await file.read()

        uploaded_files.append(
            {
                "filename": filename,
                "content_type": file.content_type,
                "size": len(content),
            }
        )

    return {
        "message": "Files received successfully",
        "agent": agent,
        "files": uploaded_files,
    }