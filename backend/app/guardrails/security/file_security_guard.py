import logging
from pathlib import Path

from app.guardrails.models import GuardrailResult
from app.guardrails.security.security_policy import DEFAULT_SECURITY_POLICY
from app.services.file_type_service import is_supported_document_extension


logger = logging.getLogger(__name__)


class FileSecurityGuardrail:
    """Validates uploaded file metadata before persistence."""

    @staticmethod
    def validate_upload(
        *,
        filename: str | None,
        content_type: str | None = None,
        extension: str | None = None,
        size: int | None = None,
        max_size: int | None = None,
    ) -> GuardrailResult:
        if not DEFAULT_SECURITY_POLICY.enable_file_security:
            return GuardrailResult.pass_(
                code="FILE_SECURITY_DISABLED",
                reason="File security guardrail is disabled by policy.",
            )

        raw_name = str(filename or "")
        safe_name = Path(raw_name).name
        effective_extension = (extension or Path(safe_name).suffix).lower()
        metadata = {
            "source_type": "upload_file",
            "extension": effective_extension,
            "content_type": content_type,
            "size": size,
        }

        if not raw_name.strip() or not safe_name.strip():
            return FileSecurityGuardrail._block(
                code="UNSAFE_FILENAME",
                reason="Uploaded file has no usable filename.",
                metadata=metadata,
            )

        if raw_name != safe_name or any(token in raw_name for token in ("..", "/", "\\")):
            return FileSecurityGuardrail._block(
                code="UNSAFE_FILENAME",
                reason="Uploaded filename contains path traversal characters.",
                metadata=metadata,
            )

        if len(safe_name) > DEFAULT_SECURITY_POLICY.max_filename_characters:
            return FileSecurityGuardrail._block(
                code="UNSAFE_FILENAME",
                reason="Uploaded filename exceeds the configured length limit.",
                metadata={**metadata, "filename_length": len(safe_name)},
            )

        if not is_supported_document_extension(effective_extension):
            return FileSecurityGuardrail._block(
                code="UNSUPPORTED_FILE_TYPE",
                reason="Uploaded file extension is not supported.",
                metadata=metadata,
            )

        if size is not None and size <= 0:
            return FileSecurityGuardrail._block(
                code="EMPTY_FILE",
                reason="Uploaded file is empty.",
                metadata=metadata,
            )

        if size is not None and max_size is not None and size > max_size:
            return FileSecurityGuardrail._block(
                code="FILE_TOO_LARGE",
                reason="Uploaded file exceeds the configured size limit.",
                metadata={**metadata, "max_size": max_size},
            )

        result = GuardrailResult.pass_(
            code="FILE_SECURITY_PASS",
            reason="Uploaded file metadata passed security checks.",
            metadata=metadata,
        )
        FileSecurityGuardrail._log(result)
        return result

    @staticmethod
    def _block(*, code: str, reason: str, metadata: dict) -> GuardrailResult:
        result = GuardrailResult.block(code=code, reason=reason, metadata=metadata)
        FileSecurityGuardrail._log(result)
        return result

    @staticmethod
    def _log(result: GuardrailResult) -> None:
        logger_method = logger.warning if not result.passed else logger.info
        logger_method(
            "security_guard=file status=%s code=%s source_type=%s",
            result.status.value,
            result.code,
            result.metadata.get("source_type"),
        )
