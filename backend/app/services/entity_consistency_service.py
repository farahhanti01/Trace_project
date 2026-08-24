from typing import Any

from app.services.generic_question_classifier_service import QueryPlan


ENTITY_KEYS = ("field_number", "message_type", "concept", "chapter", "section")


def first_value(value: Any) -> str | None:
    if isinstance(value, list):
        return str(value[0]) if value else None

    return str(value) if value else None


def target_entities_from_plan(plan: QueryPlan) -> dict[str, str]:
    targets: dict[str, str] = {}
    entities = plan.entities or {}

    for key in ENTITY_KEYS:
        value = first_value(entities.get(key) or entities.get(f"{key}s"))

        if value:
            targets[key] = value

    return targets


def entity_bag(unit: dict[str, Any], key: str) -> set[str]:
    values: set[str] = set()

    for container_name in ("entities", "primary_entities", "referenced_entities"):
        container = unit.get(container_name) or {}
        raw_value = container.get(key) or container.get(f"{key}s")

        if isinstance(raw_value, list):
            values.update(str(item) for item in raw_value if item is not None)
        elif raw_value is not None:
            values.add(str(raw_value))

    return values


def primary_entity_bag(unit: dict[str, Any], key: str) -> set[str]:
    values: set[str] = set()

    for container_name in ("primary_entities", "entities"):
        container = unit.get(container_name) or {}
        raw_value = container.get(key) or container.get(f"{key}s")

        if isinstance(raw_value, list):
            values.update(str(item) for item in raw_value if item is not None)
        elif raw_value is not None:
            values.add(str(raw_value))

    return values


def referenced_entity_bag(unit: dict[str, Any], key: str) -> set[str]:
    container = unit.get("referenced_entities") or {}
    raw_value = container.get(key) or container.get(f"{key}s")

    if isinstance(raw_value, list):
        return set(str(item) for item in raw_value if item is not None)

    if raw_value is not None:
        return {str(raw_value)}

    return set()


def unit_entity_alignment(
    unit: dict[str, Any],
    targets: dict[str, str],
) -> str:
    if not targets:
        return "contextual"

    for key, target in targets.items():
        if target in primary_entity_bag(unit, key):
            return "primary_match"

    for key, target in targets.items():
        if target in referenced_entity_bag(unit, key):
            return "referenced_match"

    for key, target in targets.items():
        values = entity_bag(unit, key)

        if values and target not in values:
            return "mismatch"

    return "contextual"


def filter_units_for_entity_consistency(
    units: list[dict[str, Any]],
    plan: QueryPlan,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    targets = target_entities_from_plan(plan)
    aligned_units = [
        {
            **unit,
            "entity_alignment": unit_entity_alignment(unit, targets),
        }
        for unit in units
    ]

    if not targets:
        return aligned_units, {
            "target_entities": targets,
            "removed_mismatches": 0,
        }

    strict_intents = {
        "FIELD_LOOKUP",
        "VALUE_LOOKUP",
        "TABLE_LOOKUP",
        "RULE_LOOKUP",
    }

    if plan.intent not in strict_intents:
        return aligned_units, {
            "target_entities": targets,
            "removed_mismatches": 0,
        }

    filtered = [
        unit
        for unit in aligned_units
        if unit.get("entity_alignment") != "mismatch"
    ]

    if not filtered:
        return aligned_units, {
            "target_entities": targets,
            "removed_mismatches": 0,
            "filter_fallback": "all_units_were_mismatch",
        }

    return filtered, {
        "target_entities": targets,
        "removed_mismatches": len(aligned_units) - len(filtered),
    }


def check_evidence_completeness(
    *,
    plan: QueryPlan,
    units: list[dict[str, Any]],
) -> list[str]:
    missing: list[str] = []

    if plan.intent != "RULE_LOOKUP":
        return missing

    target_types = {"rule", "field_usage", "field_attribute", "exception", "note"}
    has_rule_evidence = any(
        unit.get("content_type") in target_types
        for unit in units
    )

    if not has_rule_evidence:
        missing.append("rule, usage, attribute, exception or note evidence")

    return missing
