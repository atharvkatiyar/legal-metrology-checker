
"""
Rule Engine & Compliance Logic (Role 4)

Presence checks (Aug 25-26 scope) are live. Format/content checks
(Aug 28+ scope, once Role 1's legal spec + Role 3's confirmed field
list land) hook into the same Violation model and can now attach a
real bbox (see check_presence's bbox wiring below) once they exist.
"""

from app.schemas.contracts import (
    BBox,
    FieldMappingOutput,
    ExtractedField,
    ComplianceResult,
    Violation,
)

# Confirmed against app/services/field_mapping_adapter.py's _FIELD_KEY_MAP
# target keys + _UNSUPPORTED_FIELDS. If Field Mapping's output keys ever
# change, update the adapter -- not this list -- so the two stay in sync
# from a single source of truth.
MANDATORY_FIELDS = [
    "mrp",
    "net_quantity",
    "manufacturer",
    "mfg_date",
    "consumer_care",
]

# country_of_origin is intentionally excluded from MANDATORY_FIELDS.
# It's only legally required for IMPORTED products, not universal --
# and Role 1's labeled_batch/ dataset doesn't track it at all yet.
# Re-add it once:
#   1. Field Mapping starts extracting it, AND
#   2. We know how to determine whether a given product is imported
# Confirmed with team: still pending as of Aug 31.

SEVERITY_DEDUCTIONS = {
    "critical": 30,
    "major": 15,
    "minor": 5,
}

# Per-field severity for presence violations. Falls back to "critical"
# for any mandatory field not listed here (safer default than silently
# under-penalizing an unrecognized field).
PRESENCE_SEVERITY = {
    "mrp": "critical",
    "net_quantity": "critical",
    "manufacturer": "critical",
    "mfg_date": "major",
    "consumer_care": "major",
    
}


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

    bbox is intentionally None here: a MISSING field has no location on
    the image to box -- there is nothing to draw a red box around. This
    is correct behavior, not a gap. Format/content checks (Aug 28+),
    which validate a field that IS present but wrong, should instead
    pull `field.bbox` from the corresponding ExtractedField (already a
    proper BBox thanks to the adapter) and pass it through to Violation
    so the frontend overlay can point at the exact wrong text.
    """
    violations: list[Violation] = []

    for field_name in MANDATORY_FIELDS:
        field = fields.get(field_name)

        if not is_field_present(field):
            violations.append(
                Violation(
                    field_name=field_name,
                    issue=f"'{field_name}' is missing from the label",
                    severity=PRESENCE_SEVERITY.get(field_name, "critical"),
                    bbox=None,
                    legal_reference=None,
                )
            )

    return violations


def check_formats(fields: dict[str, ExtractedField]) -> list[Violation]:
    """
    Placeholder entry point for Aug 28+ format/content validation
    (exact MRP wording, valid date ranges, valid units, etc.), kept
    separate from presence checks so the two concerns don't tangle.

    Not wired into check_compliance() yet -- there are no real format
    rules to enforce until Role 1's legal spec is finalized. When they
    land, each check here should build its Violation with:

        bbox=fields[field_name].bbox

    so violations on PRESENT-but-invalid fields carry real coordinates
    into the red-box overlay, exactly the way presence violations
    correctly carry bbox=None for fields that aren't there at all.
    """
    return []


def compute_score(violations: list[Violation]) -> float:
    """
    Simple deduction-based scoring. Starts at 100, subtracts per
    violation based on severity, floors at 0.
    """
    score = 100.0
    for v in violations:
        score -= SEVERITY_DEDUCTIONS.get(v.severity, 0)
    return max(score, 0.0)


def check_compliance(mapping_output: FieldMappingOutput) -> ComplianceResult:
    """
    Main entry point.

    Current scope (Aug 25-26): presence checks only.
    Future scope (Aug 28+): check_formats() above, once real rules
    and Role 3's confirmed field list land.
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


# --- Quick manual test with mock data ---
if __name__ == "__main__":
    mock_fields = {
        "mrp": ExtractedField(
            field_name="mrp",
            raw_value="₹45 (Inclusive of all taxes)",
            normalized_value=45.0,
            bbox=BBox(xmin=10.0, ymin=20.0, xmax=100.0, ymax=50.0),
            confidence=0.92,
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
    }

    mock_input = FieldMappingOutput(fields=mock_fields)
    result = check_compliance(mock_input)

    print("is_compliant:", result.is_compliant)
    print("score:", result.score)
    print("violations:")
    for v in result.violations:
        print(f"  - {v.field_name}: {v.issue} ({v.severity}) bbox={v.bbox}")