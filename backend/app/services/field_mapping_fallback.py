from __future__ import annotations

import asyncio
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

GEMINI_TIMEOUT_SECONDS = 12.0


def _is_missing_regex_result(
    field_result: Optional[Dict[str, Any]],
) -> bool:
    """
    Return True when deterministic Field Mapping did not produce
    a usable value.
    """
    if not isinstance(field_result, dict):
        return True

    value = field_result.get("value")

    if value is None:
        return True

    return field_result.get("confidence", "none") == "none"


def _missing_fields(
    mapping_result: Dict[str, Any],
) -> List[str]:
    """
    Return fields unresolved by deterministic Field Mapping.
    """
    return [
        field_name
        for field_name in SUPPORTED_FIELDS
        if _is_missing_regex_result(
            mapping_result.get(field_name)
        )
    ]


def _merge_gemini_fallback(
    mapping_result: Dict[str, Any],
    gemini_result: Dict[str, Any],
    missing_fields: List[str],
) -> Dict[str, Any]:
    """
    Fill only fields that deterministic Field Mapping did not resolve.

    Successful deterministic results are never overwritten by Gemini.
    """
    merged = dict(mapping_result)

    for field_name in missing_fields:
        gemini_value = gemini_result.get(field_name)

        if gemini_value is None:
            continue

        existing = merged.get(field_name)

        if not isinstance(existing, dict):
            continue

        if existing.get("value") is not None:
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


async def _call_gemini_with_timeout(
    image_path: str,
) -> Dict[str, Any]:
    """
    Run the synchronous Gemini client in a worker thread so the FastAPI
    event loop is not blocked.

    Timeout is applied at the async boundary.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(
            extract_fields_from_image,
            image_path,
        ),
        timeout=GEMINI_TIMEOUT_SECONDS,
    )


async def map_fields_with_fallback(
    ocr_input: Any,
    image_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Regex-first Field Mapping with optional Gemini fallback.

    Flow:
        1. Run deterministic regex Field Mapping.
        2. Detect unresolved fields.
        3. Return immediately if everything is resolved.
        4. Return immediately if no image is available for Gemini.
        5. Call Gemini asynchronously in a worker thread.
        6. Abort the Gemini attempt after the configured timeout.
        7. Fill only fields that regex failed to resolve.
        8. On any Gemini failure, preserve the deterministic result.

    Gemini is therefore an optional enhancement and never a hard
    dependency for the pipeline.
    """
    mapping_result = map_fields(ocr_input)

    missing_fields = _missing_fields(
        mapping_result
    )

    if not missing_fields:
        return mapping_result

    if not image_path:
        return mapping_result

    try:
        gemini_result = await _call_gemini_with_timeout(
            image_path
        )

    except asyncio.TimeoutError:
        logger.warning(
            "Gemini fallback timed out after %.1f seconds; "
            "returning deterministic Field Mapping result",
            GEMINI_TIMEOUT_SECONDS,
        )
        return mapping_result

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

    if not isinstance(gemini_result, dict):
        return mapping_result

    return _merge_gemini_fallback(
        mapping_result,
        gemini_result,
        missing_fields,
    )


__all__ = [
    "SUPPORTED_FIELDS",
    "GEMINI_TIMEOUT_SECONDS",
    "map_fields_with_fallback",
]