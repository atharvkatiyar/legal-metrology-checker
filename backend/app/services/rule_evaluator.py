"""
rule_evaluator.py

Dynamic, JSON-driven rule engine for Legal Metrology label compliance.

Contract for `extracted_entities`:
    Dict[str, Optional[dict]] where each present key's value is the
    ExtractedField shape from app/schemas/contracts.py, i.e. the dict
    produced by:

        field_mapping_adapter.build_field_mapping_output(mapping_result)
            .fields[field_name]
            .model_dump()

    i.e. {
        "field_name": str,
        "raw_value": Optional[str],
        "normalized_value": Optional[Any],
        "bbox": Optional[dict],   # {"xmin":..,"ymin":..,"xmax":..,"ymax":..}
        "confidence": float,
        "method": "regex" | "llm" | "none",
    }

    This is a deliberate choice per Rules.md #2: Rule Engine (and this
    evaluator) consumes the Integration Lead's adapter contract only,
    never field_mapping's raw uppercase-keyed dict directly.

rules.json driven -- no hardcoded per-field branching. Each rule may
optionally set "check_target" (a dot-path into the ExtractedField dict,
e.g. "raw_value" or "normalized_value.unit") to select which sub-value
regex/unit checks run against. If omitted, checks default to
"normalized_value", falling back to "raw_value" if that's empty.
"""

import json
import re
from typing import Any, Dict, List, Optional


class LMPCRuleEngine:
    def __init__(self, rules_json_path: str) -> None:
        with open(rules_json_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        # Tolerate both {"rules": [...]} and a list-wrapped
        # [{"rules": [...]}, ...] shape without crashing on either.
        if isinstance(loaded, list):
            rules: List[Dict[str, Any]] = []
            for block in loaded:
                if isinstance(block, dict):
                    rules.extend(block.get("rules", []) or [])
            self.rules_config: List[Dict[str, Any]] = rules
        elif isinstance(loaded, dict):
            self.rules_config = loaded.get("rules", []) or []
        else:
            self.rules_config = []

    # ------------------------------------------------------------------
    # Value resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_empty(value: Any) -> bool:
        """Treat None, empty string/collection, and whitespace-only
        strings as 'not present'. Never raises on odd types."""
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        if isinstance(value, (list, dict, tuple, set)):
            return len(value) == 0
        return False

    @staticmethod
    def _resolve_path(entity: Any, dotted_path: str) -> Any:
        """Safely walk a dotted path (e.g. 'normalized_value.unit')
        through nested dicts. Returns None on any missing key, wrong
        type, or malformed path -- never raises KeyError/TypeError."""
        current = entity
        for part in dotted_path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _entity_is_present(self, entity: Any) -> bool:
        """A field counts as 'present' only if the ExtractedField-shaped
        entry exists, has a non-empty raw_value, and wasn't resolved
        via method='none' (mirrors rule_engine.is_field_present)."""
        if not isinstance(entity, dict):
            return False
        if self._is_empty(entity.get("raw_value")):
            return False
        if entity.get("method") == "none":
            return False
        return True

    def _resolve_check_value(
        self,
        rule: Dict[str, Any],
        entity: Any,
    ) -> Any:
        """Resolve the value a regex/unit check should run against.

        Explicit 'check_target' wins. Otherwise default to
        normalized_value, falling back to raw_value when
        normalized_value is empty -- so a rule author who doesn't
        specify check_target still gets a sensible value to check."""
        check_target = rule.get("check_target")

        if check_target:
            return self._resolve_path(entity, check_target)

        if not isinstance(entity, dict):
            return None

        normalized = entity.get("normalized_value")
        if not self._is_empty(normalized):
            return normalized

        return entity.get("raw_value")

    # ------------------------------------------------------------------
    # Individual check types
    # ------------------------------------------------------------------

    def _check_mandatory(
        self,
        rule: Dict[str, Any],
        entity: Any,
    ) -> Optional[Dict[str, Any]]:
        if not rule.get("mandatory", False):
            return None
        if self._entity_is_present(entity):
            return None

        field = rule.get("field", "unknown_field")
        description = rule.get("description", "")

        return {
            "clause": rule.get("clause", ""),
            "issue": (
                f"'{field}' is missing from the label"
                + (f" — required: {description}" if description else "")
            ),
            "penalty": rule.get("penalty_ref", ""),
        }

    def _check_regex(
        self,
        rule: Dict[str, Any],
        entity: Any,
    ) -> Optional[Dict[str, Any]]:
        pattern = rule.get("regex_pattern")
        if not pattern:
            return None

        value = self._resolve_check_value(rule, entity)
        if self._is_empty(value):
            return None

        text = value if isinstance(value, str) else str(value)

        try:
            matched = re.search(pattern, text, flags=re.IGNORECASE) is not None
        except re.error:
            # Malformed pattern in config must never crash the pipeline.
            return None

        if matched:
            return None

        field = rule.get("field", "unknown_field")

        return {
            "clause": rule.get("clause", ""),
            "issue": f"'{field}' value does not match the required format for this rule",
            "penalty": rule.get("penalty_ref", ""),
        }

    def _check_valid_units(
        self,
        rule: Dict[str, Any],
        entity: Any,
    ) -> Optional[Dict[str, Any]]:
        valid_units = rule.get("valid_units")
        if not valid_units:
            return None

        value = self._resolve_check_value(rule, entity)
        if self._is_empty(value):
            return None

        text = value if isinstance(value, str) else str(value)
        text_lower = text.lower()

        has_valid_unit = any(
            isinstance(unit, str) and unit.lower() == text_lower.strip()
            for unit in valid_units
        ) or any(
            isinstance(unit, str) and unit.lower() in text_lower
            for unit in valid_units
        )

        if has_valid_unit:
            return None

        field = rule.get("field", "unknown_field")
        units_display = ", ".join(str(u) for u in valid_units)

        return {
            "clause": rule.get("clause", ""),
            "issue": (
                f"'{field}' value does not contain a recognized unit "
                f"(expected one of: {units_display})"
            ),
            "penalty": rule.get("penalty_ref", ""),
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def validate_extracted_label(
        self,
        extracted_entities: Dict[str, Any],
    ) -> Dict[str, Any]:
        violations: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []

        if not isinstance(extracted_entities, dict):
            extracted_entities = {}

        for rule in self.rules_config:
            if not isinstance(rule, dict):
                continue

            field = rule.get("field")
            if not field:
                continue

            entity = extracted_entities.get(field)
            severity = str(rule.get("severity", "")).upper()

            found_violation: Optional[Dict[str, Any]] = None

            mandatory_result = self._check_mandatory(rule, entity)
            if mandatory_result is not None:
                found_violation = mandatory_result
            else:
                regex_result = self._check_regex(rule, entity)
                if regex_result is not None:
                    found_violation = regex_result
                else:
                    unit_result = self._check_valid_units(rule, entity)
                    if unit_result is not None:
                        found_violation = unit_result

            if found_violation is None:
                continue

            if severity == "WARNING":
                warnings.append(found_violation)
            else:
                violations.append(found_violation)

        return {
            "is_compliant": len(violations) == 0,
            "total_violations": len(violations),
            "violations": violations,
            "warnings": warnings,
        }