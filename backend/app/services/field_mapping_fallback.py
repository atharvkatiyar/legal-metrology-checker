from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.field_mapping import map_fields
from app.services.gemini_service import (
    GeminiExtractionError,
    extract_fields_from_image,
)

logger = logging.getLogger(__name__)


SUPPORTED_FIELDS = (
    "MRP",
    "NET_QUANTITY",
    "MANUFACTURER_ADDRESS",
    "MANUFACTURING_DATE",
    "CONSUMER_CARE",
)


def _is_missing_regex_result(
    field_result: Optional[Dict[str, Any]],
) -> bool:
    """
    Decide whether the deterministic Field Mapping result failed to
    produce a usable value.

    Only genuinely missing/none results trigger the LLM fallback.
    Existing regex results are never overwritten here.
    """
    if not isinstance(field_result, dict):
        return True

    value = field_result.get("value")

    if value is None:
        return True

    confidence = field_result.get(
        "confidence",
        "none",
    )

    return confidence == "none"


def _missing_fields(
    mapping_result: Dict[str, Any],
) -> List[str]:
    """
    Return only fields that the regex mapper failed to resolve.
    """
    missing: List[str] = []

    for field_name in SUPPORTED_FIELDS:
        if _is_missing_regex_result(
            mapping_result.get(field_name)
        ):
            missing.append(field_name)

    return missing


def _merge_gemini_fallback(
    mapping_result: Dict[str, Any],
    gemini_result: Dict[str, Any],
    missing_fields: List[str],
) -> Dict[str, Any]:
    """
    Fill only fields that regex failed to resolve.

    Gemini is never allowed to overwrite a successful deterministic
    extraction.
    """
    merged = dict(mapping_result)

    for field_name in missing_fields:
        gemini_value = gemini_result.get(
            field_name
        )

        if gemini_value is None:
            continue

        existing = merged.get(field_name)

        if not isinstance(existing, dict):
            continue

        existing_value = existing.get("value")

        if existing_value is not None:
            continue

        existing["value"] = gemini_value
        existing["confidence"] = "high"
        existing["raw_evidence"] = gemini_value
        existing["all_candidates"] = [
            {
                "field": field_name,
                "value": gemini_value,
                "label_matched": "Gemini fallback",
                "score": 0.8,
                "span": [0, 0],
                "reasons": [
                    "deterministic Field Mapping "
                    "did not resolve the field",
                    "value supplied by Gemini fallback",
                ],
            }
        ]

        merged[field_name] = existing

    return merged


def map_fields_with_fallback(
    ocr_input: Any,
    image_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Regex-first Field Mapping with optional Gemini fallback.

    Flow:
        1. Run deterministic Field Mapping.
        2. Identify unresolved fields.
        3. If all fields are resolved, return immediately.
        4. If unresolved fields exist and image_path is supplied,
           call Gemini.
        5. Fill only unresolved fields.
        6. If Gemini fails, return the original regex result.

    The function is deliberately fail-safe:
    Gemini is an optional enhancement and never becomes a hard
    dependency for deterministic Field Mapping.
    """
    mapping_result = map_fields(
        ocr_input
    )

    missing_fields = _missing_fields(
        mapping_result
    )

    if not missing_fields:
        return mapping_result

    if not image_path:
        return mapping_result

    try:
        gemini_result = extract_fields_from_image(
            image_path
        )

    except (
        GeminiExtractionError,
        FileNotFoundError,
        OSError,
    ):
        logger.exception(
            "Gemini fallback failed; "
            "returning deterministic Field Mapping result"
        )
        return mapping_result

    except Exception:
        logger.exception(
            "Unexpected Gemini fallback error; "
            "returning deterministic Field Mapping result"
        )
        return mapping_result

    if not isinstance(
        gemini_result,
        dict,
    ):
        return mapping_result

    return _merge_gemini_fallback(
        mapping_result,
        gemini_result,
        missing_fields,
    )


__all__ = [
    "SUPPORTED_FIELDS",
    "map_fields_with_fallback",
]