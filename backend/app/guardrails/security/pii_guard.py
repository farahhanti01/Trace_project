import logging
import re
from dataclasses import dataclass, field

from app.guardrails.models import GuardrailResult, GuardrailStatus
from app.guardrails.security.security_policy import DEFAULT_SECURITY_POLICY


logger = logging.getLogger(__name__)


EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?\d[\s.-]?){9,15}(?!\d)"
)
IPV4_PATTERN = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
)


@dataclass
class PIIFinding:
    code: str
    start: int
    end: int
    replacement: str


@dataclass
class PIIReport:
    text: str
    redacted_text: str
    findings: list[PIIFinding] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return self.text != self.redacted_text


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    masked_local = f"{local[:1]}***" if local else "***"
    return f"{masked_local}@{domain}"


def mask_digits(value: str) -> str:
    digits = re.sub(r"\D", "", value)

    if len(digits) < 9:
        return value

    return f"[REDACTED_PHONE:{digits[-2:]}]"


class PIIGuardrail:
    """Detects simple PII patterns and masks them when policy allows it."""

    @staticmethod
    def inspect_text(text: str) -> PIIReport:
        value = str(text or "")
        findings: list[PIIFinding] = []

        for match in EMAIL_PATTERN.finditer(value):
            findings.append(
                PIIFinding(
                    code="EMAIL_DETECTED",
                    start=match.start(),
                    end=match.end(),
                    replacement=mask_email(match.group(0)),
                )
            )

        for match in PHONE_PATTERN.finditer(value):
            digits = re.sub(r"\D", "", match.group(0))

            if len(digits) < 9:
                continue

            findings.append(
                PIIFinding(
                    code="PHONE_DETECTED",
                    start=match.start(),
                    end=match.end(),
                    replacement=mask_digits(match.group(0)),
                )
            )

        redacted = value

        if DEFAULT_SECURITY_POLICY.pii_mode == "MASK":
            for finding in sorted(findings, key=lambda item: item.start, reverse=True):
                redacted = redacted[:finding.start] + finding.replacement + redacted[finding.end:]

        report = PIIReport(text=value, redacted_text=redacted, findings=findings)
        PIIGuardrail._log(report)
        return report

    @staticmethod
    def validate(text: str, *, source_type: str = "text") -> GuardrailResult:
        report = PIIGuardrail.inspect_text(text)
        metadata = {
            "source_type": source_type,
            "finding_codes": sorted({finding.code for finding in report.findings}),
            "redacted": report.redacted,
            "mode": DEFAULT_SECURITY_POLICY.pii_mode,
        }

        if not report.findings:
            return GuardrailResult.pass_(
                code="NO_PII_DETECTED",
                reason="No simple PII pattern detected.",
                metadata=metadata,
            )

        if DEFAULT_SECURITY_POLICY.pii_mode == "BLOCK":
            return GuardrailResult.block(
                code="PII_DETECTED",
                reason="PII was detected and policy is BLOCK.",
                metadata=metadata,
            )

        if DEFAULT_SECURITY_POLICY.pii_mode == "MASK":
            return GuardrailResult.redact(
                code="PII_REDACTED",
                reason="PII was detected and masked.",
                metadata={**metadata, "redacted_text": report.redacted_text},
            )

        return GuardrailResult.warning(
            code="PII_DETECTED",
            reason="PII was detected and policy allows it.",
            metadata=metadata,
        )

    @staticmethod
    def _log(report: PIIReport) -> None:
        if not report.findings:
            return

        logger.info(
            "security_guard=pii status=%s codes=%s",
            GuardrailStatus.REDACT.value if report.redacted else GuardrailStatus.WARNING.value,
            sorted({finding.code for finding in report.findings}),
        )
