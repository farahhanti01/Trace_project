from typing import Any

from app.guardrails.models import GuardrailResult


class Iso8583Guardrail:
    """Extension point for ISO 8583 deterministic business checks.

    This first version intentionally does not invent any ISO rule. Future
    checks must be backed by parsed documentation or explicit trace metadata.
    """

    @staticmethod
    def validate_trace_fields(
        *,
        fields: dict[str, Any],
        documented_rules: list[dict[str, Any]] | None = None,
    ) -> GuardrailResult:
        return GuardrailResult.pass_(
            code="ISO8583_RULES_NOT_CONFIGURED",
            reason="No ISO8583 business guardrail rules are configured yet.",
            metadata={
                "fields_count": len(fields),
                "documented_rules_count": len(documented_rules or []),
            },
        )
