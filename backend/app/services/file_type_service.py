import re
from typing import Any


TRACE_TEXT_EXTENSIONS = {
    ".txt",
    ".log",
}
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}
TRACE_EXTENSION_REGEX = r"^\.trc\d+$"
TRACE_EXTENSION_PATTERN = re.compile(TRACE_EXTENSION_REGEX, flags=re.IGNORECASE)

REFERENCE_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    *IMAGE_EXTENSIONS,
}

ALLOWED_DOCUMENT_EXTENSIONS = {
    *REFERENCE_DOCUMENT_EXTENSIONS,
    *TRACE_TEXT_EXTENSIONS,
}


def normalize_extension(extension: str | None) -> str:
    """Normalise une extension de fichier avec le point initial."""
    value = str(extension or "").strip().lower()

    if value and not value.startswith("."):
        value = f".{value}"

    return value


def is_trace_extension(extension: str | None) -> bool:
    """Indique si une extension correspond a une trace texte."""
    value = normalize_extension(extension)

    return value in TRACE_TEXT_EXTENSIONS or bool(
        TRACE_EXTENSION_PATTERN.fullmatch(value)
    )


def is_image_extension(extension: str | None) -> bool:
    """Indique si une extension correspond a une image exploitable."""
    value = normalize_extension(extension)

    return value in IMAGE_EXTENSIONS


def is_supported_document_extension(extension: str | None) -> bool:
    """Verifie si le document peut etre accepte par la plateforme."""
    value = normalize_extension(extension)

    return value in ALLOWED_DOCUMENT_EXTENSIONS or is_trace_extension(value)


def trace_extension_query() -> dict[str, Any]:
    """Filtre MongoDB pour retrouver les traces texte."""
    return {
        "$or": [
            {"extension": {"$in": sorted(TRACE_TEXT_EXTENSIONS)}},
            {"extension": {"$regex": TRACE_EXTENSION_REGEX}},
        ]
    }


def with_trace_extension_query(
    query: dict[str, Any],
) -> dict[str, Any]:
    """Ajoute le filtre trace a une requete MongoDB existante."""
    return {
        **query,
        **trace_extension_query(),
    }
