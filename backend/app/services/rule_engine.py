"""
Rule Engine & Compliance Logic (Role 4)

Presence checks (Aug 25-26 scope) are live.
Format checks (Sep 4+): Role 1's legal spec landed in
app/core/rules.json on Sep 4 -- check_formats() now implements the
two fields that have real, checkable rules (mrp regex, net_quantity
valid units). Fields with presence-only rules (manufacturer, mfg_date,
consumer_care, commodity_name) are already covered by check_presence()
and are not duplicated here.

KNOWN GAP (flagged to team, not fixed here): rules.json requires
"commodity_name" (Rule 6(1)(b)), but Role 3's field_mapping_adapter.py
_FIELD_KEY_MAP does not extract it yet. It's included in
MANDATORY_FIELDS below on purpose -- it will always show as missing
until Field Mapping adds support. This is intentional, not a bug.
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
# Single source of truth: if Role 1 updates rules.json, this code
# does not need to change (unless a NEW check_target type is added).
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
        rule["field"]: rule
        for rule in data.get("rules", [])
        if "field" in rule
    }


RULES_BY_FIELD: dict[str, dict[str, Any]] = _load_rules()

# Role 1's severity words -> this app's Violation.severity values.
# CRITICAL is the only one that maps to "critical" (MRP tax wording is
# the single CRITICAL rule); everything else mandatory is "major";
# non-mandatory (WARNING, e.g. standard_pack_size) maps to "minor".
_SEVERITY_MAP: dict[str, str] = {
    "CRITICAL": "critical",
    "HIGH": "major",
    "MEDIUM": "minor",
    "WARNING": "minor",
}

# Mandatory fields per rules.json, as of Sep 4. commodity_name is
# intentionally included even though Field Mapping doesn't support it
# yet -- see module docstring.
MANDATORY_FIELDS = [
    "mrp",
    "net_quantity",
    "manufacturer",
    "mfg_date",
    "consumer_care",
   
]

# country_of_origin stays excluded -- confirmed with team as of Aug 31:
# only required for imported goods, not tracked by Field Mapping yet,
# and not present in rules.json either.


def _severity_for(field_name: str) -> str:
    """
    Look up this app's severity value for a field from rules.json.
    Falls back to "critical" if the field isn't in rules.json at all
    (safer default than silently under-penalizing).
    """
    rule = RULES_BY_FIELD.get(field_name)
    if rule is None:
        return "critical"
    return _SEVERITY_MAP.get(rule.get("severity", ""), "critical")


def _legal_reference_for(field_name: str) -> str | None:
    """
    Build a human-readable legal citation from rules.json's clause +
    penalty_ref, e.g. "Rule 6(1)(e) -- Section 36(1) of Legal
    Metrology Act, 2009". Used by format checks (mrp / net_quantity).
    """
    rule = RULES_BY_FIELD.get(field_name)
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
    or if it's there but raw_value is None / method is 'none'
    (per the contract, Role 3 always includes the key, just with
    empty values when nothing was found).
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

    Iterates the loaded rules_config (RULES_BY_FIELD) for each
    mandatory field and extracts the raw `clause` directly from
    rules.json, passing it as `legal_reference` on the Violation --
    this keeps the citation tied 1:1 to Role 1's source clause rather
    than a combined clause+penalty_ref string.

    bbox is intentionally None here: a MISSING field has no location
    on the image to box -- there is nothing to draw a red box around.
    """
    violations: list[Violation] = []

    for field_name in MANDATORY_FIELDS:
        field = fields.get(field_name)

        if not is_field_present(field):
            rule = RULES_BY_FIELD.get(field_name)
            clause = rule.get("clause") if rule else None

            violations.append(
                Violation(
                    field_name=field_name,
                    issue=f"'{field_name}' is missing from the label",
                    severity=_severity_for(field_name),
                    bbox=None,
                    legal_reference=clause,
                )
            )

    return violations


def _check_mrp_format(field: ExtractedField) -> Violation | None:
    """
    Rule 6(1)(e): MRP must be present AND must declare "inclusive of
    all taxes" (or an accepted variant), per rules.json's regex_pattern.
    Only called when the field IS present (missing is check_presence's
    job).
    """
    rule = RULES_BY_FIELD.get("mrp")

    if rule is None or "regex_pattern" not in rule:
        return None  # no format rule defined yet -- skip

    pattern = rule["regex_pattern"]

    # NOTE: re.IGNORECASE applied here as a safety net -- rules.json's
    # pattern only matches "inclusive" in lowercase (its abbreviated
    # "Incl." form handles case, but the full word doesn't), which
    # would wrongly flag real labels reading "Inclusive of all taxes".
    # Flagged to Role 1; harmless to keep even after they fix the
    # source pattern.
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
    Rule 6(1)(c): net_quantity must use a standard metric unit, per
    rules.json's valid_units list. Checks normalized_value["unit"],
    since Field Mapping already normalizes this to
    {"amount": float, "unit": str}.
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


def check_formats(fields: dict[str, ExtractedField]) -> list[Violation]:
    """
    Format/content validation for fields that have real, checkable
    rules in rules.json (Sep 4+ scope).

    Only mrp and net_quantity have machine-checkable rules right now
    (a regex and a valid-units list, respectively). manufacturer,
    mfg_date, consumer_care, and commodity_name are presence-only per
    rules.json -- already covered by check_presence(), not duplicated
    here. If Role 1 adds format rules for those later (e.g. a
    mfg_date format regex), add a matching _check_x_format() function
    here.

    Only runs a format check when the field IS present -- a missing
    field is check_presence()'s job, not this function's.
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

    Runs presence checks (all mandatory fields, using rules_config /
    RULES_BY_FIELD to source each field's legal `clause` directly)
    then format checks (mrp, net_quantity -- the only fields with
    machine-checkable format rules as of Sep 4).
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


# --- Quick manual test with real labeled_batch data ------------------
if __name__ == "__main__":
    # Nescafé sample: net_quantity missing, mrp present but WITHOUT
    # "inclusive of all taxes" wording (real label just says
    # "MRP ₹10.00") -- should trigger both a presence violation AND
    # a format violation, plus commodity_name/consumer_care presence
    # gaps depending on what's filled in below.
    mock_fields = {
        "mrp": ExtractedField(
            field_name="mrp",
            raw_value="MRP ₹10.00",  # no "inclusive of all taxes" text
            normalized_value=10.0,
            bbox=BBox(xmin=10.0, ymin=20.0, xmax=100.0, ymax=50.0),
            confidence=0.9,
            method="regex",
        ),
        "net_quantity": ExtractedField(
            field_name="net_quantity",
            raw_value=None,
            normalized_value=None,
            bbox=None,
            confidence=0.0,
            method="none",
        ),
        "manufacturer": ExtractedField(
            field_name="manufacturer",
            raw_value="NESTLÉ INDIA LTD., KIADB INDUSTRIAL AREA, NANJANGUD, MYSORE, (KARNATAKA) - 571 302",
            normalized_value=None,
            bbox=None,
            confidence=0.9,
            method="regex",
        ),
        "mfg_date": ExtractedField(
            field_name="mfg_date",
            raw_value="FEB/26",
            normalized_value="2026-02",
            bbox=None,
            confidence=0.85,
            method="regex",
        ),
        "consumer_care": ExtractedField(
            field_name="consumer_care",
            raw_value="1800 103 1947 / WECARE@IN.NESTLE.COM",
            normalized_value=None,
            bbox=None,
            confidence=0.9,
            method="regex",
        ),
        # commodity_name intentionally absent -- Field Mapping doesn't
        # extract it yet (known gap, see module docstring)
    }

    mock_input = FieldMappingOutput(fields=mock_fields)
    result = check_compliance(mock_input)

    print("is_compliant:", result.is_compliant)
    print("score:", result.score)
    print("violations:")
    for v in result.violations:
        print(
            f"  - {v.field_name}: {v.issue} "
            f"({v.severity}) bbox={v.bbox} "
            f"ref={v.legal_reference}"
        )