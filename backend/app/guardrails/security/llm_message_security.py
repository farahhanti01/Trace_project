import copy
import logging
from typing import Any

from app.guardrails.models import GuardrailResult, GuardrailStatus
from app.guardrails.security.document_security_guard import DocumentSecurityGuardrail
from app.guardrails.security.security_policy import (
    DEFAULT_SECURITY_POLICY,
    SECURITY_UNTRUSTED_CONTENT_RULE,
)
from app.guardrails.security.trace_security_guard import TraceSecurityGuardrail


logger = logging.getLogger(__name__)


def secure_llm_messages(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], GuardrailResult]:
    """Adds central security instruction and redacts text before LLM calls."""

    if not DEFAULT_SECURITY_POLICY.enable_llm_redaction:
        return messages, GuardrailResult.pass_(
            code="LLM_SECURITY_DISABLED",
            reason="LLM security redaction is disabled by policy.",
        )

    secured_messages = copy.deepcopy(messages)
    redacted_count = 0
    finding_codes = []

    for message in secured_messages:
        role = message.get("role")
        content = message.get("content")

        if role == "system" and isinstance(content, str):
            message["content"] = f"{SECURITY_UNTRUSTED_CONTENT_RULE}\n\n{content}"
            continue

        secured_content, changed, codes = secure_content_part(content)
        message["content"] = secured_content

        if changed:
            redacted_count += 1
            finding_codes.extend(codes)

    if not any(message.get("role") == "system" for message in secured_messages):
        secured_messages.insert(
            0,
            {
                "role": "system",
                "content": SECURITY_UNTRUSTED_CONTENT_RULE,
            },
        )

    if redacted_count:
        result = GuardrailResult.redact(
            code="LLM_INPUT_REDACTED",
            reason="Sensitive content was redacted before the LLM call.",
            metadata={
                "source_type": "llm_messages",
                "redacted_messages": redacted_count,
                "finding_codes": sorted(set(finding_codes)),
            },
        )
    else:
        result = GuardrailResult.pass_(
            code="LLM_INPUT_SECURITY_PASS",
            reason="LLM messages passed security checks.",
            metadata={"source_type": "llm_messages"},
        )

    log = logger.warning if result.status != GuardrailStatus.PASS else logger.info
    log(
        "security_guard=llm_input status=%s code=%s source_type=%s",
        result.status.value,
        result.code,
        result.metadata.get("source_type"),
    )
    return secured_messages, result


def secure_content_part(content: Any) -> tuple[Any, bool, list[str]]:
    if isinstance(content, str):
        secured, document_result = DocumentSecurityGuardrail.secure_text(
            content,
            source_type="llm_text",
        )
        secured, trace_result = TraceSecurityGuardrail.secure_text(
            secured,
            source_type="llm_text",
        )
        return (
            secured,
            secured != content,
            [document_result.code, trace_result.code],
        )

    if isinstance(content, list):
        secured_items = []
        changed = False
        codes = []

        for item in content:
            secured_item, item_changed, item_codes = secure_content_part(item)
            secured_items.append(secured_item)
            changed = changed or item_changed
            codes.extend(item_codes)

        return secured_items, changed, codes

    if isinstance(content, dict):
        secured_dict = {}
        changed = False
        codes = []

        for key, value in content.items():
            if key == "image_url":
                secured_dict[key] = value
                continue

            secured_value, value_changed, value_codes = secure_content_part(value)
            secured_dict[key] = secured_value
            changed = changed or value_changed
            codes.extend(value_codes)

        return secured_dict, changed, codes

    return content, False, []
