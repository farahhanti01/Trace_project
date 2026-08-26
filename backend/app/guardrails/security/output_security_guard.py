import json
import logging
from typing import Any

from app.guardrails.models import GuardrailResult, GuardrailStatus
from app.guardrails.security.prompt_injection_guard import SYSTEM_DISCLOSURE_PATTERN
from app.guardrails.security.secret_guard import SecretGuardrail
from app.guardrails.security.security_policy import DEFAULT_SECURITY_POLICY


logger = logging.getLogger(__name__)


class OutputSecurityGuardrail:
    """Prevents generated responses from exposing secrets or internal prompts."""

    @staticmethod
    def secure_response(response: dict[str, Any]) -> tuple[dict[str, Any], GuardrailResult]:
        if not DEFAULT_SECURITY_POLICY.enable_output_security:
            return response, GuardrailResult.pass_(
                code="OUTPUT_SECURITY_DISABLED",
                reason="Output security guardrail is disabled by policy.",
            )

        serialized = json.dumps(response, ensure_ascii=False, default=str)

        if SYSTEM_DISCLOSURE_PATTERN.search(serialized):
            result = GuardrailResult.block(
                code="SYSTEM_PROMPT_DISCLOSURE_ATTEMPT",
                reason="Response appears to expose internal prompt/instruction content.",
                metadata={"source_type": "assistant_output"},
            )
            OutputSecurityGuardrail._log(result)
            return OutputSecurityGuardrail.blocked_response(result), result

        secured, redacted = OutputSecurityGuardrail._redact_value(response)

        if redacted:
            result = GuardrailResult.redact(
                code="OUTPUT_SECRET_LEAK",
                reason="Sensitive output content was redacted before returning to the user.",
                metadata={"source_type": "assistant_output"},
            )
            OutputSecurityGuardrail._log(result)
            return secured, result

        result = GuardrailResult.pass_(
            code="OUTPUT_SECURITY_PASS",
            reason="Output passed security checks.",
            metadata={"source_type": "assistant_output"},
        )
        OutputSecurityGuardrail._log(result)
        return response, result

    @staticmethod
    def blocked_response(result: GuardrailResult) -> dict[str, Any]:
        return {
            "summary": "La reponse a ete bloquee par les controles de securite.",
            "sections": [
                {
                    "title": "Controle securite",
                    "content": (
                        "TRACE a detecte un risque de fuite d'instructions "
                        "internes ou de donnees sensibles dans la reponse."
                    ),
                    "source_ids": [],
                }
            ],
            "story": [],
            "issues": [
                {
                    "severity": "error",
                    "title": result.code,
                    "detail": "La reponse candidate n'a pas ete exposee.",
                }
            ],
            "recommendations": [
                "Reformule la question sans demander d'instructions internes ou de secrets.",
            ],
            "references": [],
            "evidence": [],
            "transactions": [],
            "statistics": {},
            "display_options": {},
        }

    @staticmethod
    def _redact_value(value: Any) -> tuple[Any, bool]:
        if isinstance(value, str):
            report = SecretGuardrail.inspect_text(value)
            return report.redacted_text, report.redacted

        if isinstance(value, list):
            secured_items = []
            changed = False

            for item in value:
                secured_item, item_changed = OutputSecurityGuardrail._redact_value(item)
                secured_items.append(secured_item)
                changed = changed or item_changed

            return secured_items, changed

        if isinstance(value, dict):
            secured_dict = {}
            changed = False

            for key, item in value.items():
                secured_item, item_changed = OutputSecurityGuardrail._redact_value(item)
                secured_dict[key] = secured_item
                changed = changed or item_changed

            return secured_dict, changed

        return value, False

    @staticmethod
    def _log(result: GuardrailResult) -> None:
        log = logger.warning if result.status != GuardrailStatus.PASS else logger.info
        log(
            "security_guard=output_security status=%s code=%s source_type=%s",
            result.status.value,
            result.code,
            result.metadata.get("source_type"),
        )
