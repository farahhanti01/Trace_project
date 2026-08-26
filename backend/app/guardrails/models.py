from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GuardrailStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    REDACT = "REDACT"
    BLOCK = "BLOCK"


class GuardrailResult(BaseModel):
    status: GuardrailStatus
    code: str
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status != GuardrailStatus.BLOCK

    @classmethod
    def pass_(
        cls,
        *,
        code: str = "PASS",
        reason: str = "Guardrail passed.",
        metadata: dict[str, Any] | None = None,
    ) -> "GuardrailResult":
        return cls(
            status=GuardrailStatus.PASS,
            code=code,
            reason=reason,
            metadata=metadata or {},
        )

    @classmethod
    def warning(
        cls,
        *,
        code: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> "GuardrailResult":
        return cls(
            status=GuardrailStatus.WARNING,
            code=code,
            reason=reason,
            metadata=metadata or {},
        )

    @classmethod
    def redact(
        cls,
        *,
        code: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> "GuardrailResult":
        return cls(
            status=GuardrailStatus.REDACT,
            code=code,
            reason=reason,
            metadata=metadata or {},
        )

    @classmethod
    def block(
        cls,
        *,
        code: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> "GuardrailResult":
        return cls(
            status=GuardrailStatus.BLOCK,
            code=code,
            reason=reason,
            metadata=metadata or {},
        )
