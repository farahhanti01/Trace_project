import logging
import re
from dataclasses import dataclass, field

from app.guardrails.models import GuardrailResult, GuardrailStatus
from app.guardrails.security.prompt_injection_guard import PromptInjectionGuardrail
from app.guardrails.security.secret_guard import SecretGuardrail


logger = logging.getLogger(__name__)


PAN_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(\d[ -]?){13,19}(?!\d)")
TRACK_DATA_PATTERN = re.compile(r"%?B\d{13,19}\^[^^]{0,32}\^[0-9]{4}", re.IGNORECASE)


@dataclass
class TraceSecurityReport:
    text: str
    secured_text: str
    findings: list[str] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return self.text != self.secured_text


def luhn_valid(number: str) -> bool:
    digits = [int(character) for character in number if character.isdigit()]

    if len(digits) < 13 or len(digits) > 19:
        return False

    total = 0
    parity = len(digits) % 2

    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2

            if digit > 9:
                digit -= 9

        total += digit

    return total % 10 == 0


def mask_pan(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)

    if not luhn_valid(digits):
        return raw

    return f"{'*' * max(len(digits) - 4, 0)}{digits[-4:]}"


class TraceSecurityGuardrail:
    """Redacts sensitive trace data before indexing or LLM usage."""

    @staticmethod
    def secure_text(text: str, *, source_type: str = "trace") -> tuple[str, GuardrailResult]:
        value = str(text or "")
        secured = value
        findings: list[str] = []

        secret_report = SecretGuardrail.inspect_text(secured)

        if secret_report.redacted:
            secured = secret_report.redacted_text
            findings.extend(finding.code for finding in secret_report.findings)

        if secret_report.has_blocking_secret:
            result = GuardrailResult.block(
                code="PRIVATE_KEY_DETECTED",
                reason="A private key was detected in trace content.",
                metadata={"source_type": source_type, "finding_codes": sorted(set(findings))},
            )
            TraceSecurityGuardrail._log(result)
            return secured, result

        secured = TRACK_DATA_PATTERN.sub("[REDACTED_TRACK_DATA]", secured)

        if secured != secret_report.redacted_text:
            findings.append("TRACK_DATA_DETECTED")

        pan_redacted = PAN_CANDIDATE_PATTERN.sub(mask_pan, secured)

        if pan_redacted != secured:
            findings.append("PAN_LIKE_VALUE_REDACTED")
            secured = pan_redacted

        injection_result = PromptInjectionGuardrail.validate(
            value,
            source_type=source_type,
        )

        if not injection_result.passed:
            findings.append(injection_result.code)

        status = GuardrailStatus.REDACT if secured != value else GuardrailStatus.PASS

        if findings and status == GuardrailStatus.PASS:
            result = GuardrailResult.warning(
                code="TRACE_SECURITY_WARNING",
                reason="Potential prompt injection found in trace data; treated as data.",
                metadata={"source_type": source_type, "finding_codes": sorted(set(findings))},
            )
        elif status == GuardrailStatus.REDACT:
            result = GuardrailResult.redact(
                code="SENSITIVE_TRACE_DATA",
                reason="Sensitive trace content was redacted.",
                metadata={"source_type": source_type, "finding_codes": sorted(set(findings))},
            )
        else:
            result = GuardrailResult.pass_(
                code="TRACE_SECURITY_PASS",
                reason="Trace content passed security checks.",
                metadata={"source_type": source_type},
            )

        TraceSecurityGuardrail._log(result)
        return secured, result

    @staticmethod
    def _log(result: GuardrailResult) -> None:
        log = logger.warning if result.status != GuardrailStatus.PASS else logger.info
        log(
            "security_guard=trace_security status=%s code=%s source_type=%s",
            result.status.value,
            result.code,
            result.metadata.get("source_type"),
        )
