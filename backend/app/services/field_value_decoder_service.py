import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

from app.models.document_content import ContentUnit, EvidenceBundle


class FieldValueSegment(BaseModel):
    position: str
    component: str
    value: str
    meaning: str
    usage: str = ""
    source_id: str = ""


class FieldValueDecoding(BaseModel):
    field_number: str
    raw_value: str
    rows: list[FieldValueSegment] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    complete: bool = False
    issues: list[str] = Field(default_factory=list)


class _PositionGroup(BaseModel):
    start: int
    end: int
    name: str
    source_id: str
    mappings: dict[str, dict[str, str]] = Field(default_factory=dict)

    @property
    def label(self) -> str:
        if self.start == self.end:
            return f"Position {self.start}"

        return f"Positions {self.start}-{self.end}"

    @property
    def width(self) -> int:
        return self.end - self.start + 1


def normalize_decoder_text(value: Any) -> str:
    text = str(value or "")
    text = (
        text.replace("â€“", "-")
        .replace("â€”", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("\u00a0", " ")
    )
    decomposed = unicodedata.normalize("NFKD", text)
    text = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize_field_number(value: Any) -> str:
    raw = str(value or "").strip()

    if not raw:
        return ""

    if "." in raw:
        left, right = raw.split(".", 1)
        return f"{int(left):03d}.{right}" if left.isdigit() else raw

    return f"{int(raw):03d}" if raw.isdigit() else raw


def field_value_from_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().strip("[]"))


def source_lines_from_unit(unit: ContentUnit) -> list[str]:
    chunks = [
        unit.hierarchy.title,
        unit.content,
        unit.raw_text,
    ]
    lines: list[str] = []

    for chunk in chunks:
        for line in str(chunk or "").splitlines():
            cleaned = normalize_decoder_text(line)

            if cleaned:
                lines.append(cleaned)

    return lines


def content_unit_matches_field(unit: ContentUnit, field_number: str) -> bool:
    expected = normalize_field_number(field_number)

    for container in (unit.entities, unit.primary_entities, unit.referenced_entities):
        value = container.get("field_number")

        if value and normalize_field_number(value) == expected:
            return True

        values = container.get("field_numbers")

        if isinstance(values, list) and any(
            normalize_field_number(item) == expected
            for item in values
        ):
            return True

    text = " ".join([
        str(unit.hierarchy.title or ""),
        str(unit.content or ""),
        str(unit.raw_text or ""),
    ])

    return bool(re.search(
        rf"\b(?:field|fld|champ)\s*0*{re.escape(expected.lstrip('0') or expected)}\b",
        text,
        flags=re.IGNORECASE,
    ))


def parse_position_marker(line: str) -> tuple[int, int, str] | None:
    text = normalize_decoder_text(line)
    match = re.search(
        r"\bpositions?\s+(\d+)\s*(?:-\s*(\d+))?\s*(?:,|:|-)?\s*([^:\n.;]{0,90})",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    name = normalize_decoder_text(match.group(3))
    name = re.sub(r"\b(a|an)\s+\d+\s*[- ]?digit\b.*$", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"\s+", " ", name).strip(" :-")

    if not name or name.lower() in {"continued"}:
        name = f"Position {start}" if start == end else f"Positions {start}-{end}"

    return start, end, name


def split_cells(line: str) -> list[str]:
    if "|" not in line:
        return []

    return [
        normalize_decoder_text(cell)
        for cell in line.split("|")
        if normalize_decoder_text(cell)
    ]


def parse_mapping_line(line: str, width: int) -> tuple[str, str, str] | None:
    text = normalize_decoder_text(line).strip()

    if not text or re.match(r"^(code|definition|meaning|usage)\b", text, flags=re.IGNORECASE):
        return None

    cells = split_cells(text)

    if len(cells) >= 2:
        code = cells[0].strip("'\"").upper()

        if len(code) == width and re.search(r"\d|[A-Z]", code):
            return code, cells[1], " ".join(cells[2:])

    match = re.match(
        rf"^['\"]?([A-Z0-9]{{{width}}})['\"]?\s+(.+)$",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    code = match.group(1).upper()
    meaning = normalize_decoder_text(match.group(2))

    if not meaning or meaning.lower().startswith(("visa confidential", "chapter ", "table ")):
        return None

    return code, meaning, ""


def update_position_group(
    groups: dict[tuple[int, int], _PositionGroup],
    *,
    source_id: str,
    marker: tuple[int, int, str],
) -> _PositionGroup:
    start, end, name = marker
    key = (start, end)
    group = groups.get(key)

    if not group:
        group = _PositionGroup(
            start=start,
            end=end,
            name=name,
            source_id=source_id,
        )
        groups[key] = group
    elif group.name == group.label and name:
        group.name = name

    return group


def decode_field_value_from_sources(
    *,
    field_number: str,
    value: Any,
    sources: list[dict[str, Any]],
) -> FieldValueDecoding | None:
    normalized_field = normalize_field_number(field_number)
    raw_value = field_value_from_text(value)

    if not normalized_field or not raw_value:
        return None

    groups: dict[tuple[int, int], _PositionGroup] = {}
    current_group: _PositionGroup | None = None

    for source in sources:
        source_id = str(source.get("source_id") or source.get("unit_id") or "")

        for raw_line in str(source.get("text") or "").splitlines():
            line = normalize_decoder_text(raw_line)

            if not line:
                continue

            marker = parse_position_marker(line)

            if marker:
                current_group = update_position_group(
                    groups,
                    source_id=source_id,
                    marker=marker,
                )
                continue

            if not current_group:
                continue

            parsed_mapping = parse_mapping_line(line, current_group.width)

            if not parsed_mapping:
                continue

            code, meaning, usage = parsed_mapping
            current_group.mappings.setdefault(
                code,
                {
                    "meaning": meaning,
                    "usage": usage,
                    "source_id": source_id or current_group.source_id,
                },
            )

    if not groups:
        return None

    rows: list[FieldValueSegment] = []
    issues: list[str] = []
    source_ids: list[str] = []

    for group in sorted(groups.values(), key=lambda item: (item.start, item.end)):
        observed = (
            raw_value[group.start - 1:group.end]
            if len(raw_value) >= group.end
            else ""
        )
        mapping = group.mappings.get(observed) if observed else None

        if not observed:
            issues.append(
                f"{group.label} absente de la valeur observee {raw_value}."
            )
        elif not mapping:
            issues.append(
                f"Aucun mapping documentaire confirme pour {group.label} = {observed}."
            )

        source_id = (
            mapping.get("source_id")
            if mapping
            else group.source_id
        )

        if source_id:
            source_ids.append(source_id)

        rows.append(
            FieldValueSegment(
                position=group.label,
                component=group.name,
                value=observed or "Non present",
                meaning=mapping.get("meaning", "Non confirme dans l'evidence") if mapping else "Non confirme dans l'evidence",
                usage=mapping.get("usage", "") if mapping else "",
                source_id=source_id or "",
            )
        )

    if not rows:
        return None

    return FieldValueDecoding(
        field_number=normalized_field,
        raw_value=raw_value,
        rows=rows,
        source_ids=list(dict.fromkeys(source_ids)),
        complete=not issues,
        issues=issues,
    )


def decode_field_value_from_evidence(
    *,
    bundle: EvidenceBundle,
    field_number: str,
    value: Any,
) -> FieldValueDecoding | None:
    normalized_field = normalize_field_number(field_number)
    ordered_units = sorted(
        [
            unit
            for unit in bundle.retrieved_units
            if content_unit_matches_field(unit, normalized_field)
        ],
        key=lambda unit: (
            unit.source or "",
            unit.document_order if unit.document_order is not None else 10**9,
            unit.unit_id,
        ),
    )
    sources = [
        {
            "source_id": unit.unit_id,
            "text": "\n".join(source_lines_from_unit(unit)),
        }
        for unit in ordered_units
    ]

    return decode_field_value_from_sources(
        field_number=normalized_field,
        value=value,
        sources=sources,
    )


def field_value_decoding_table_block(decoding: FieldValueDecoding) -> dict[str, Any]:
    return {
        "type": "table",
        "title": f"Field {decoding.field_number} - decodage par positions",
        "columns": [
            {"key": "position", "label": "Position"},
            {"key": "component", "label": "Sous-champ"},
            {"key": "value", "label": "Valeur observee"},
            {"key": "meaning", "label": "Signification documentaire"},
            {"key": "usage", "label": "Usage"},
        ],
        "rows": [
            {
                "position": row.position,
                "component": row.component,
                "value": row.value,
                "meaning": row.meaning,
                "usage": row.usage,
                "evidence_id": row.source_id,
            }
            for row in decoding.rows
        ],
    }
