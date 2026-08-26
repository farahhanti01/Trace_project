import logging

from app.guardrails.models import GuardrailResult, GuardrailStatus
from app.guardrails.security.pii_guard import PIIGuardrail
from app.guardrails.security.prompt_injection_guard import PromptInjectionGuardrail
from app.guardrails.security.secret_guard import SecretGuardrail
from app.guardrails.security.security_policy import DEFAULT_SECURITY_POLICY


logger = logging.getLogger(__name__)


class InputSecurityGuardrail:
    """Security checks executed before routing a chat question."""

    @staticmethod
    def validate(question: str) -> GuardrailResult:
        if not DEFAULT_SECURITY_POLICY.enable_input_security:
            return GuardrailResult.pass_(
                code="INPUT_SECURITY_DISABLED",
                reason="Input security guardrail is disabled by policy.",
            )

        text = str(question or "")
        stripped = text.strip()

        if not stripped:
            return InputSecurityGuardrail._block(
                code="EMPTY_INPUT",
                reason="Question is empty.",
                metadata={"source_type": "chat_input"},
            )

        if len(stripped) > DEFAULT_SECURITY_POLICY.max_chat_input_characters:
            return InputSecurityGuardrail._block(
                code="INPUT_TOO_LARGE",
                reason="Question exceeds the configured input size limit.",
                metadata={
                    "source_type": "chat_input",
                    "text_length": len(stripped),
                    "max_length": DEFAULT_SECURITY_POLICY.max_chat_input_characters,
                },
            )

        prompt_result = PromptInjectionGuardrail.validate(
            stripped,
            source_type="chat_input",
        )

        if prompt_result.status == GuardrailStatus.BLOCK:
            return prompt_result

        secret_result = SecretGuardrail.validate(
            stripped,
            source_type="chat_input",
        )

        if secret_result.status == GuardrailStatus.BLOCK:
            return secret_result

        if secret_result.status == GuardrailStatus.REDACT:
            return secret_result

        pii_result = PIIGuardrail.validate(
            stripped,
            source_type="chat_input",
        )

        if pii_result.status == GuardrailStatus.BLOCK:
            return pii_result

        if pii_result.status == GuardrailStatus.REDACT:
            return pii_result

        if prompt_result.status == GuardrailStatus.WARNING:
            return prompt_result

        if secret_result.status == GuardrailStatus.WARNING:
            return secret_result

        if pii_result.status == GuardrailStatus.WARNING:
            return pii_result

        result = GuardrailResult.pass_(
            code="INPUT_SECURITY_PASS",
            reason="Input passed security checks.",
            metadata={"source_type": "chat_input"},
        )
        InputSecurityGuardrail._log(result)
        return result

    @staticmethod
    def _block(*, code: str, reason: str, metadata: dict) -> GuardrailResult:
        result = GuardrailResult.block(code=code, reason=reason, metadata=metadata)
        InputSecurityGuardrail._log(result)
        return result

    @staticmethod
    def _log(result: GuardrailResult) -> None:
        log = logger.warning if result.status != GuardrailStatus.PASS else logger.info
        log(
            "security_guard=input status=%s code=%s source_type=%s",
            result.status.value,
            result.code,
            result.metadata.get("source_type"),
        )
