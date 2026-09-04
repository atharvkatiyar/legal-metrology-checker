from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.schemas.contracts import BBox, ExtractedField, FieldMappingOutput

# Maps the field_mapping package's output keys to the lowercase snake_case
# names the Rule Engine's MANDATORY_FIELDS list expects.
_FIELD_KEY_MAP: Dict[str, str] = {
    "MRP": "mrp",
    "NET_QUANTITY": "net_quantity",
    "MANUFACTURER_ADDRESS": "manufacturer",
    "MANUFACTURING_DATE": "mfg_date",
    "CONSUMER_CARE": "consumer_care",
}

# field_mapping.py reports confidence as "high" / "low" / "none". The
# contract requires a float in [0.0, 1.0]. These are deliberately
# conservative, deterministic mappings -- not calibrated probabilities.
_CONFIDENCE_MAP: Dict[str, float] = {
    "high": 0.9,
    "low": 0.4,
    "none": 0.0,
}

# Fields the Rule Engine expects (per its MANDATORY_FIELDS list) that
# Field Mapping does not currently extract at all. Emitted as genuinely
# "none" / missing rather than silently omitted, so presence checks
# correctly flag them until a real extractor exists.
_UNSUPPORTED_FIELDS: List[str] = ["country_of_origin"]


def _normalized_value_to_raw_string(value: Any) -> Optional[str]:
    """ExtractedField.raw_value is a plain string. field_mapping's `value`
    can be a float (MRP), a dict (NET_QUANTITY / CONSUMER_CARE), a string
    (MANUFACTURER_ADDRESS), or an ISO date string (MANUFACTURING_DATE).
    Stringify anything that isn't already a string or None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _polygons_to_bbox(polygons: Optional[List[Any]]) -> Optional[BBox]:
    """field_mapping's `bbox` (only present on MANUFACTURER_ADDRESS,
    MANUFACTURING_DATE, CONSUMER_CARE) is a list of per-token 4-point
    polygons: [[[x,y],[x,y],[x,y],[x,y]], ...]. MRP and NET_QUANTITY never
    carry bbox at all (plain FieldResult, not ExtendedFieldResult).

    Collapses every point across every token polygon into one bounding
    box. Returns None if there are no usable points -- never fabricates
    coordinates."""
    if not polygons:
        return None

    xs: List[float] = []
    ys: List[float] = []
    for polygon in polygons:
        if not isinstance(polygon, (list, tuple)):
            continue
        for point in polygon:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                x, y = float(point[0]), float(point[1])
            except (TypeError, ValueError):
                continue
            xs.append(x)
            ys.append(y)

    if not xs or not ys:
        return None

    return BBox(xmin=min(xs), ymin=min(ys), xmax=max(xs), ymax=max(ys))


def _to_extracted_field(field_name: str, raw: Dict[str, Any]) -> ExtractedField:
    confidence_label = raw.get("confidence", "none")
    confidence = _CONFIDENCE_MAP.get(confidence_label, 0.0)

    value = raw.get("value")
    method = "regex" if value is not None else "none"

    return ExtractedField(
        field_name=field_name,
        raw_value=_normalized_value_to_raw_string(raw.get("raw_evidence")),
        normalized_value=value,
        bbox=_polygons_to_bbox(raw.get("bbox")),
        confidence=confidence,
        method=method,
    )


def build_field_mapping_output(mapping_result: Dict[str, Any]) -> FieldMappingOutput:
    """Converts app.field_mapping.map_fields()'s raw dict output into the
    FieldMappingOutput/ExtractedField contract the Rule Engine consumes.

    Never assumes a key is present -- map_fields() is the source of truth
    for which keys exist; this only translates the ones it recognizes and
    fills in known-unsupported fields as empty/none so presence checks
    behave correctly rather than crashing on a missing key."""
    fields: Dict[str, ExtractedField] = {}

    for source_key, target_key in _FIELD_KEY_MAP.items():
        raw = mapping_result.get(source_key)
        if not isinstance(raw, dict):
            # Defensive: if map_fields() ever omits a key, still emit a
            # well-formed "not found" field rather than skipping it.
            fields[target_key] = ExtractedField(
                field_name=target_key,
                raw_value=None,
                normalized_value=None,
                bbox=None,
                confidence=0.0,
                method="none",
            )
            continue
        fields[target_key] = _to_extracted_field(target_key, raw)

    for target_key in _UNSUPPORTED_FIELDS:
        fields[target_key] = ExtractedField(
            field_name=target_key,
            raw_value=None,
            normalized_value=None,
            bbox=None,
            confidence=0.0,
            method="none",
        )

    return FieldMappingOutput(fields=fields)