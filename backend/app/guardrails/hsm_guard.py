from typing import Any

from app.guardrails.models import GuardrailResult


class HsmGuardrail:
    """Extension point for HSM deterministic business checks.

    This first version only defines the integration contract. Concrete HSM
    rules must come from trace structure or documentation evidence.
    """

    @staticmethod
    def validate_exchange(
        *,
        hsm_exchange: dict[str, Any],
        documented_rules: list[dict[str, Any]] | None = None,
    ) -> GuardrailResult:
        return GuardrailResult.pass_(
            code="HSM_RULES_NOT_CONFIGURED",
            reason="No HSM business guardrail rules are configured yet.",
            metadata={
                "has_to_hsm": bool(hsm_exchange.get("to_hsm")),
                "has_from_hsm": bool(hsm_exchange.get("from_hsm")),
                "documented_rules_count": len(documented_rules or []),
            },
        )
