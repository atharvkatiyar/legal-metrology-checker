"""
Rule Engine & Compliance Logic (Role 4)

Presence checks are live. Format checks implement the fields that have 
real, checkable rules (mrp regex, net_quantity valid units, font_size thresholds). 
Fields with presence-only rules (manufacturer, mfg_date, consumer_care) 
are already covered by check_presence() and are not duplicated here.

NOTE: country_of_origin and generic_name (commodity_name) have been formally 
deprecated and removed from rules.json and the enforcement matrix.
"""

import json
import re
from pathlib import Path
from typing import Any

from app.schemas.contracts import (
    BBox,
    FieldMappingOutput,
    ExtractedField,
    ComplianceResult,
    Violation,
)

# --- Load Role 1's legal spec ---------------------------------------
_RULES_PATH = Path(__file__).resolve().parent.parent / "core" / "rules.json"


def _load_rules() -> dict[str, dict[str, Any]]:
    """
    Load rules.json and index it by field name for fast lookup.
    Falls back to an empty dict if the file is missing/invalid, so a
    bad rules.json degrades to "no format checks" instead of crashing
    the whole app.
    """
    try:
        with open(_RULES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    return {
        rule["field"].lower(): rule
        for rule in data.get("rules", [])
        if "field" in rule
    }


RULES_BY_FIELD: dict[str, dict[str, Any]] = _load_rules()

# Role 1's severity words -> this app's Violation.severity values.
_SEVERITY_MAP: dict[str, str] = {
    "CRITICAL": "critical",
    "HIGH": "major",
    "MEDIUM": "minor",
    "WARNING": "minor",
}

# Mandatory fields per rules.json. 
MANDATORY_FIELDS = [
    "mrp",
    "net_quantity",
    "manufacturer",
    "mfg_date",
    "consumer_care",
    "FONT_SIZE"
]

def _severity_for(field_name: str) -> str:
    """
    Look up this app's severity value for a field from rules.json.
    Falls back to "critical" if the field isn't in rules.json at all.
    """
    rule = RULES_BY_FIELD.get(field_name.lower())
    if rule is None:
        return "critical"
    return _SEVERITY_MAP.get(rule.get("severity", ""), "critical")


def _legal_reference_for(field_name: str) -> str | None:
    """
    Build a human-readable legal citation from rules.json's clause + penalty_ref.
    """
    rule = RULES_BY_FIELD.get(field_name.lower())
    if rule is None:
        return None

    clause = rule.get("clause")
    penalty_ref = rule.get("penalty_ref")

    if clause and penalty_ref:
        return f"{clause} -- {penalty_ref}"
    return clause


def is_field_present(field: ExtractedField | None) -> bool:
    """
    A field counts as 'missing' if it's not in the dict at all,
    or if it's there but raw_value is None / method is 'none'.
    """
    if field is None:
        return False
    if field.raw_value is None:
        return False
    if field.method == "none":
        return False
    return True


def check_presence(fields: dict[str, ExtractedField]) -> list[Violation]:
    """
    Presence-only checks for every mandatory field.
    """
    violations: list[Violation] = []

    for field_name in MANDATORY_FIELDS:
        field = fields.get(field_name)

        if not is_field_present(field):
            rule = RULES_BY_FIELD.get(field_name.lower())
            clause = rule.get("clause") if rule else None

            # Custom warning message for the Font Calibrator
            if field_name == "FONT_SIZE":
                issue_msg = "Rule 9 Font Calibration is pending or could not be auto-detected (requires manual coin tap)."
            else:
                issue_msg = f"'{field_name}' is missing from the label"

            violations.append(
                Violation(
                    field_name=field_name,
                    issue=issue_msg,
                    severity=_severity_for(field_name),
                    bbox=None,
                    legal_reference=clause,
                )
            )

    return violations


def _check_mrp_format(field: ExtractedField) -> Violation | None:
    """
    Rule 6(1)(e): MRP must be present AND must declare "inclusive of all taxes".
    """
    rule = RULES_BY_FIELD.get("mrp")

    if rule is None or "regex_pattern" not in rule:
        return None 

    pattern = rule["regex_pattern"]

    if not field.raw_value or not re.search(
        pattern, field.raw_value, re.IGNORECASE
    ):
        return Violation(
            field_name="mrp",
            issue=(
                "MRP is present but does not declare "
                "'inclusive of all taxes' as required"
            ),
            severity=_severity_for("mrp"),
            bbox=field.bbox,
            legal_reference=_legal_reference_for("mrp"),
        )

    return None


def _check_net_quantity_format(field: ExtractedField) -> Violation | None:
    """
    Rule 6(1)(c): net_quantity must use a standard metric unit.
    """
    rule = RULES_BY_FIELD.get("net_quantity")

    if rule is None or "valid_units" not in rule:
        return None

    valid_units = rule["valid_units"]
    normalized = field.normalized_value

    if not isinstance(normalized, dict) or "unit" not in normalized:
        return Violation(
            field_name="net_quantity",
            issue=(
                "net_quantity is present but its unit could not be "
                "verified against standard metric units"
            ),
            severity=_severity_for("net_quantity"),
            bbox=field.bbox,
            legal_reference=_legal_reference_for("net_quantity"),
        )

    unit = str(normalized["unit"]).lower()

    if unit not in valid_units:
        return Violation(
            field_name="net_quantity",
            issue=(
                f"net_quantity unit '{unit}' is not a standard "
                f"metric unit (expected one of: {', '.join(valid_units)})"
            ),
            severity=_severity_for("net_quantity"),
            bbox=field.bbox,
            legal_reference=_legal_reference_for("net_quantity"),
        )

    return None


def _check_font_size_format(field: ExtractedField) -> Violation | None:
    """
    Rule 9: Validates the font size. 
    If the CV calibration notes indicate a FAIL, flag it here.
    """
    if field.notes and "FAIL" in field.notes:
        return Violation(
            field_name="FONT_SIZE",
            issue=f"Text height ({field.raw_value}) is below the required statutory minimum.",
            severity=_severity_for("FONT_SIZE"),
            bbox=field.bbox,
            legal_reference=_legal_reference_for("FONT_SIZE"),
        )
    return None


def check_formats(fields: dict[str, ExtractedField]) -> list[Violation]:
    """
    Format/content validation for fields that have real, checkable rules.
    """
    violations: list[Violation] = []

    mrp = fields.get("mrp")
    if is_field_present(mrp):
        v = _check_mrp_format(mrp)
        if v:
            violations.append(v)

    net_quantity = fields.get("net_quantity")
    if is_field_present(net_quantity):
        v = _check_net_quantity_format(net_quantity)
        if v:
            violations.append(v)

    font_size = fields.get("FONT_SIZE")
    if is_field_present(font_size):
        v = _check_font_size_format(font_size)
        if v:
            violations.append(v)

    return violations


def compute_score(violations: list[Violation]) -> float:
    """
    Simple deduction-based scoring. Starts at 100, subtracts per
    violation based on severity, floors at 0.
    """
    score = 100.0
    for v in violations:
        score -= SEVERITY_DEDUCTIONS.get(v.severity, 0)
    return max(score, 0.0)


SEVERITY_DEDUCTIONS = {
    "critical": 30,
    "major": 15,
    "minor": 5,
}


def check_compliance(mapping_output: FieldMappingOutput) -> ComplianceResult:
    """
    Main entry point.
    Runs presence checks then format checks.
    """
    violations: list[Violation] = []

    violations.extend(check_presence(mapping_output.fields))
    violations.extend(check_formats(mapping_output.fields))

    score = compute_score(violations)
    is_compliant = len(violations) == 0

    return ComplianceResult(
        is_compliant=is_compliant,
        violations=violations,
        score=score,
    )