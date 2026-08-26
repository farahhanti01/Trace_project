import logging

from app.guardrails.models import GuardrailResult, GuardrailStatus
from app.guardrails.security.prompt_injection_guard import PromptInjectionGuardrail
from app.guardrails.security.secret_guard import SecretGuardrail


logger = logging.getLogger(__name__)


class DocumentSecurityGuardrail:
    """Secures extracted document text before indexing/RAG usage."""

    @staticmethod
    def secure_text(text: str, *, source_type: str = "document") -> tuple[str, GuardrailResult]:
        value = str(text or "")
        secret_result = SecretGuardrail.validate(value, source_type=source_type)
        secured = secret_result.metadata.get("redacted_text", value)
        injection_result = PromptInjectionGuardrail.validate(value, source_type=source_type)
        finding_codes = []

        if secret_result.code != "NO_SECRET_DETECTED":
            finding_codes.append(secret_result.code)

        if injection_result.code != "NO_PROMPT_INJECTION":
            finding_codes.append(injection_result.code)

        if secret_result.status == GuardrailStatus.BLOCK:
            result = GuardrailResult.block(
                code=secret_result.code,
                reason="Sensitive blocking content was detected in document text.",
                metadata={"source_type": source_type, "finding_codes": finding_codes},
            )
        elif secured != value:
            result = GuardrailResult.redact(
                code="SENSITIVE_DOCUMENT_DATA",
                reason="Sensitive document content was redacted.",
                metadata={"source_type": source_type, "finding_codes": finding_codes},
            )
        elif injection_result.status == GuardrailStatus.BLOCK:
            result = GuardrailResult.warning(
                code="DOCUMENT_PROMPT_INJECTION_AS_DATA",
                reason="Prompt-injection-like text was found in a document and kept as data.",
                metadata={"source_type": source_type, "finding_codes": finding_codes},
            )
        else:
            result = GuardrailResult.pass_(
                code="DOCUMENT_SECURITY_PASS",
                reason="Document content passed security checks.",
                metadata={"source_type": source_type},
            )

        DocumentSecurityGuardrail._log(result)
        return secured, result

    @staticmethod
    def secure_extraction_result(extraction_result: dict) -> tuple[dict, GuardrailResult]:
        sections = extraction_result.get("sections") or []
        secured_sections = []
        finding_codes = []
        redacted_count = 0

        for section in sections:
            if isinstance(section, dict):
                original_text = str(section.get("text") or "")
                secured_section = dict(section)
                secured_text, result = DocumentSecurityGuardrail.secure_text(
                    original_text,
                    source_type="document_section",
                )
                secured_section["text"] = secured_text
                secured_sections.append(secured_section)
            else:
                original_text = str(section or "")
                secured_text, result = DocumentSecurityGuardrail.secure_text(
                    original_text,
                    source_type="document_section",
                )
                secured_sections.append(secured_text)

            if secured_text != original_text:
                redacted_count += 1

            if result.code != "DOCUMENT_SECURITY_PASS":
                finding_codes.append(result.code)

        secured_result = {
            **extraction_result,
            "sections": secured_sections,
        }

        if redacted_count:
            result = GuardrailResult.redact(
                code="DOCUMENT_SECTIONS_REDACTED",
                reason="One or more extracted document sections were redacted.",
                metadata={
                    "source_type": "document",
                    "redacted_sections": redacted_count,
                    "finding_codes": sorted(set(finding_codes)),
                },
            )
        elif finding_codes:
            result = GuardrailResult.warning(
                code="DOCUMENT_SECURITY_WARNING",
                reason="One or more document sections contained security-relevant text.",
                metadata={
                    "source_type": "document",
                    "finding_codes": sorted(set(finding_codes)),
                },
            )
        else:
            result = GuardrailResult.pass_(
                code="DOCUMENT_SECURITY_PASS",
                reason="Extracted document content passed security checks.",
                metadata={"source_type": "document"},
            )

        DocumentSecurityGuardrail._log(result)
        return secured_result, result

    @staticmethod
    def _log(result: GuardrailResult) -> None:
        log = logger.warning if result.status != GuardrailStatus.PASS else logger.info
        log(
            "security_guard=document_security status=%s code=%s source_type=%s",
            result.status.value,
            result.code,
            result.metadata.get("source_type"),
        )
