import logging
import re
from dataclasses import dataclass, field

from app.guardrails.models import GuardrailResult, GuardrailStatus


logger = logging.getLogger(__name__)


PLACEHOLDER_VALUES = {
    "<fake-token>",
    "<token>",
    "your_token",
    "your-token",
    "token",
    "xxx",
    "xxxx",
    "example",
    "sample",
}

PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)
BEARER_PATTERN = re.compile(
    r"\b(Authorization\s*:\s*Bearer\s+)([A-Za-z0-9._~+/=-]{5,})",
    re.IGNORECASE,
)
ASSIGNMENT_SECRET_PATTERN = re.compile(
    r"\b(api[_-]?key|client[_-]?secret|password|passwd|secret|token)\s*[:=]\s*"
    r"([^\s'\";]{6,})",
    re.IGNORECASE,
)
JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)


@dataclass
class SensitiveFinding:
    code: str
    severity: GuardrailStatus
    start: int
    end: int
    replacement: str
    placeholder: bool = False


@dataclass
class RedactionReport:
    text: str
    redacted_text: str
    findings: list[SensitiveFinding] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return self.text != self.redacted_text

    @property
    def has_blocking_secret(self) -> bool:
        return any(finding.severity == GuardrailStatus.BLOCK for finding in self.findings)

    @property
    def result_status(self) -> GuardrailStatus:
        if self.has_blocking_secret:
            return GuardrailStatus.BLOCK

        if self.redacted:
            return GuardrailStatus.REDACT

        if self.findings:
            return GuardrailStatus.WARNING

        return GuardrailStatus.PASS


def is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()

    return normalized in PLACEHOLDER_VALUES or normalized.startswith("your_")


def mask_secret(value: str) -> str:
    if len(value) <= 4:
        return "[REDACTED]"

    return f"[REDACTED:{value[-4:]}]"


def apply_replacements(text: str, findings: list[SensitiveFinding]) -> str:
    redacted = text

    for finding in sorted(findings, key=lambda item: item.start, reverse=True):
        if finding.placeholder:
            continue

        redacted = (
            redacted[:finding.start]
            + finding.replacement
            + redacted[finding.end:]
        )

    return redacted


class SecretGuardrail:
    """Detects and redacts high-confidence secrets."""

    @staticmethod
    def inspect_text(text: str) -> RedactionReport:
        value = str(text or "")
        findings: list[SensitiveFinding] = []

        for match in PRIVATE_KEY_PATTERN.finditer(value):
            findings.append(
                SensitiveFinding(
                    code="PRIVATE_KEY_DETECTED",
                    severity=GuardrailStatus.BLOCK,
                    start=match.start(),
                    end=match.end(),
                    replacement="[REDACTED_PRIVATE_KEY]",
                )
            )

        for match in BEARER_PATTERN.finditer(value):
            token = match.group(2)
            placeholder = is_placeholder(token)
            findings.append(
                SensitiveFinding(
                    code="SECRET_PLACEHOLDER_DETECTED" if placeholder else "SECRET_DETECTED",
                    severity=GuardrailStatus.WARNING if placeholder else GuardrailStatus.REDACT,
                    start=match.start(2),
                    end=match.end(2),
                    replacement=mask_secret(token),
                    placeholder=placeholder,
                )
            )

        for match in ASSIGNMENT_SECRET_PATTERN.finditer(value):
            secret_value = match.group(2)
            placeholder = is_placeholder(secret_value)
            findings.append(
                SensitiveFinding(
                    code="SECRET_PLACEHOLDER_DETECTED" if placeholder else "SECRET_DETECTED",
                    severity=GuardrailStatus.WARNING if placeholder else GuardrailStatus.REDACT,
                    start=match.start(2),
                    end=match.end(2),
                    replacement=mask_secret(secret_value),
                    placeholder=placeholder,
                )
            )

        for match in JWT_PATTERN.finditer(value):
            findings.append(
                SensitiveFinding(
                    code="SECRET_DETECTED",
                    severity=GuardrailStatus.REDACT,
                    start=match.start(),
                    end=match.end(),
                    replacement="[REDACTED_JWT]",
                )
            )

        redacted_text = apply_replacements(value, findings)
        report = RedactionReport(
            text=value,
            redacted_text=redacted_text,
            findings=findings,
        )
        SecretGuardrail._log(report)
        return report

    @staticmethod
    def validate(text: str, *, source_type: str = "text") -> GuardrailResult:
        report = SecretGuardrail.inspect_text(text)
        metadata = {
            "source_type": source_type,
            "finding_codes": sorted({finding.code for finding in report.findings}),
            "redacted": report.redacted,
        }

        if report.has_blocking_secret:
            return GuardrailResult.block(
                code="PRIVATE_KEY_DETECTED",
                reason="A private key was detected.",
                metadata={**metadata, "redacted_text": report.redacted_text},
            )

        if report.redacted:
            return GuardrailResult.redact(
                code="SECRET_DETECTED",
                reason="Sensitive credential-like data was redacted.",
                metadata={**metadata, "redacted_text": report.redacted_text},
            )

        if report.findings:
            return GuardrailResult.warning(
                code="SECRET_PLACEHOLDER_DETECTED",
                reason="Only placeholder credentials were detected.",
                metadata=metadata,
            )

        return GuardrailResult.pass_(
            code="NO_SECRET_DETECTED",
            reason="No high-confidence secret detected.",
            metadata=metadata,
        )

    @staticmethod
    def _log(report: RedactionReport) -> None:
        if not report.findings:
            return

        status = report.result_status.value
        codes = sorted({finding.code for finding in report.findings})
        logger.warning(
            "security_guard=secret status=%s codes=%s",
            status,
            codes,
        )
