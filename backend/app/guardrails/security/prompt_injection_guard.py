import logging
import re
import unicodedata

from app.guardrails.models import GuardrailResult


logger = logging.getLogger(__name__)


def normalize_security_text(value: str) -> str:
    text = " ".join(str(value or "").lower().split())
    decomposed = unicodedata.normalize("NFKD", text)

    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )


EDUCATIONAL_PATTERN = re.compile(
    r"\b(qu(?:'|e)est ce que|c'est quoi|explique|definition|definis|what is|"
    r"explain|define)\b.{0,80}\b(system prompt|prompt systeme|prompt)\b",
    re.IGNORECASE,
)

SYSTEM_DISCLOSURE_PATTERN = re.compile(
    r"\b(show|reveal|print|display|give|dump|affiche|montre|imprime|donne|"
    r"revele|devoile)\b.{0,80}\b(system prompt|developer prompt|hidden prompt|"
    r"prompt systeme|instructions internes|instructions cachees|developer "
    r"instructions|chain[- ]of[- ]thought|chaine de pensee)\b",
    re.IGNORECASE,
)

BYPASS_PATTERN = re.compile(
    r"\b(ignore|forget|disregard|override|bypass|jailbreak|oublie|ignore|"
    r"contourne|remplace|annule)\b.{0,90}\b(previous instructions|system "
    r"instructions|developer instructions|instructions precedentes|"
    r"instructions systeme|regles|rules|policy|policies)\b",
    re.IGNORECASE,
)


class PromptInjectionGuardrail:
    """Detects explicit prompt injection and internal prompt disclosure attempts."""

    @staticmethod
    def validate(text: str, *, source_type: str = "user_input") -> GuardrailResult:
        normalized = normalize_security_text(text)
        metadata = {
            "source_type": source_type,
            "text_length": len(text or ""),
        }

        if EDUCATIONAL_PATTERN.search(normalized):
            return PromptInjectionGuardrail._pass(
                code="EDUCATIONAL_PROMPT_QUESTION",
                reason="Educational question about prompts, not a bypass request.",
                metadata=metadata,
            )

        if SYSTEM_DISCLOSURE_PATTERN.search(normalized):
            return PromptInjectionGuardrail._block(
                code="SYSTEM_PROMPT_DISCLOSURE_ATTEMPT",
                reason="The input asks to reveal internal prompts or instructions.",
                metadata=metadata,
            )

        if BYPASS_PATTERN.search(normalized):
            return PromptInjectionGuardrail._block(
                code="PROMPT_INJECTION_ATTEMPT",
                reason="The input attempts to override or ignore instructions.",
                metadata=metadata,
            )

        if "system prompt" in normalized and "instructions" in normalized:
            return PromptInjectionGuardrail._warning(
                code="POSSIBLE_PROMPT_INJECTION",
                reason="The input mentions prompt/instruction control terms.",
                metadata=metadata,
            )

        return PromptInjectionGuardrail._pass(
            code="NO_PROMPT_INJECTION",
            reason="No explicit prompt injection pattern detected.",
            metadata=metadata,
        )

    @staticmethod
    def _pass(*, code: str, reason: str, metadata: dict) -> GuardrailResult:
        result = GuardrailResult.pass_(code=code, reason=reason, metadata=metadata)
        PromptInjectionGuardrail._log(result)
        return result

    @staticmethod
    def _warning(*, code: str, reason: str, metadata: dict) -> GuardrailResult:
        result = GuardrailResult.warning(code=code, reason=reason, metadata=metadata)
        PromptInjectionGuardrail._log(result)
        return result

    @staticmethod
    def _block(*, code: str, reason: str, metadata: dict) -> GuardrailResult:
        result = GuardrailResult.block(code=code, reason=reason, metadata=metadata)
        PromptInjectionGuardrail._log(result)
        return result

    @staticmethod
    def _log(result: GuardrailResult) -> None:
        log = logger.warning if not result.passed else logger.info
        log(
            "security_guard=prompt_injection status=%s code=%s source_type=%s",
            result.status.value,
            result.code,
            result.metadata.get("source_type"),
        )
