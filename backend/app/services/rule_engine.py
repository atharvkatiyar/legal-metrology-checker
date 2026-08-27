"""
Rule Engine & Compliance Logic (Role 4)
Scope: Aug 25-26 tasks only
  - Mon Aug 25: sketch pass/fail logic structure
  - Tue Aug 26: encode presence checks (skeleton) for all mandatory declarations

NOTE: This is a SKELETON. The actual field-format validation rules
(e.g. exact MRP wording, valid date formats, valid units) are NOT
implemented yet -- those depend on Role 1's finalized legal spec,
expected around Aug 28. For now, every field only gets a PRESENCE
check: is it there or not.

Draft mandatory fields list below is a placeholder -- swap in
Role 1's real list once it's shared.
"""

from app.schemas.contracts import (
    FieldMappingOutput,
    ExtractedField,
    ComplianceResult,
    Violation,
)

# --- Draft mandatory fields list (PLACEHOLDER -- confirm with Role 1) ---
MANDATORY_FIELDS = [
    "mrp",
    "net_quantity",
    "manufacturer",
    "mfg_date",
    "consumer_care",
    "country_of_origin",
]

# --- Draft severity -> score deduction mapping (YOUR design choice) ---
SEVERITY_DEDUCTIONS = {
    "critical": 30,
    "major": 15,
    "minor": 5,
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
    No format/content validation yet -- that's Aug 28+ scope.
    """
    violations: list[Violation] = []

    for field_name in MANDATORY_FIELDS:
        field = fields.get(field_name)

        if not is_field_present(field):
            violations.append(
                Violation(
                    field_name=field_name,
                    issue=f"'{field_name}' is missing from the label",
                    severity="critical",  # placeholder -- confirm per-field severity later
                    bbox=None,
                    legal_reference=None,  # fill in once Role 1 shares citations
                )
            )

    return violations


def compute_score(violations: list[Violation]) -> float:
    """
    Simple deduction-based scoring. Starts at 100, subtracts per
    violation based on severity, floors at 0.
    This mapping (SEVERITY_DEDUCTIONS) is a draft -- sanity-check
    with the team before the demo.
    """
    score = 100.0
    for v in violations:
        score -= SEVERITY_DEDUCTIONS.get(v.severity, 0)
    return max(score, 0.0)


def check_compliance(mapping_output: FieldMappingOutput) -> ComplianceResult:
    """
    Main entry point -- this is the pass/fail logic structure.

    Current scope (Aug 25-26): presence checks only.
    Future scope (Aug 28+): add per-field format/content validation
    once Role 1's rule spec and Role 3's confirmed field list land --
    e.g. check_mrp_format(), check_date_format(), check_unit_validity().
    """
    violations: list[Violation] = []

    # 1. Presence checks (today's scope)
    violations.extend(check_presence(mapping_output.fields))

    # 2. Format/content checks (TODO once real rules are available)
    # violations.extend(check_formats(mapping_output.fields))

    # 3. Score + verdict
    score = compute_score(violations)
    is_compliant = len(violations) == 0

    return ComplianceResult(
        is_compliant=is_compliant,
        violations=violations,
        score=score,
    )


# --- Quick manual test with mock data ---
if __name__ == "__main__":
    from app.schemas.contracts import ExtractedField

    mock_fields = {
        "mrp": ExtractedField(
            field_name="mrp",
            raw_value="₹45 (Inclusive of all taxes)",
            normalized_value=45.0,
            confidence=0.92,
            method="regex",
        ),
        "net_quantity": ExtractedField(
            field_name="net_quantity",
            raw_value=None,
            normalized_value=None,
            confidence=0.0,
            method="none",
        ),
        # manufacturer, mfg_date, consumer_care, country_of_origin
        # all missing in this mock -- should show up as violations
    }

    mock_input = FieldMappingOutput(fields=mock_fields)
    result = check_compliance(mock_input)

    print("is_compliant:", result.is_compliant)
    print("score:", result.score)
    print("violations:")
    for v in result.violations:
        print(f"  - {v.field_name}: {v.issue} ({v.severity})")