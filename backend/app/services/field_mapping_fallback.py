from __future__ import annotations

import asyncio
import logging
import re
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

GEMINI_TIMEOUT_SECONDS = 60.0


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

    # Low-confidence deterministic results are considered unresolved.
    # Gemini may validate/replace them, while high-confidence regex results
    # remain protected from overwrite.
    return field_result.get("confidence", "none") in {"none", "low"}


def _missing_fields(
    mapping_result: Dict[str, Any],
) -> List[str]:
    """
    Return fields unresolved by deterministic Field Mapping.
    """
    return [
        field_name
        for field_name in SUPPORTED_FIELDS
        if _is_missing_regex_result(mapping_result.get(field_name))
    ]


def _normalize_mrp(value: Any) -> Any:
    """
    Normalize Gemini MRP output to the deterministic Field Mapping shape.

    Examples:
        "Rs. 301.00" -> 301.0
        "₹ 650"       -> 650.0
        "822.00"      -> 822.0
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    text = re.sub(
        r"(?i)\b(?:rs\.?|inr)\b",
        "",
        text,
    )
    text = text.replace("₹", "")
    text = text.replace("/-", "")
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    text = text.replace(",", "")
    text = text.strip()

    match = re.search(
        r"\d+(?:\.\d{1,2})?",
        text,
    )

    if not match:
        return value

    try:
        return float(match.group(0))
    except ValueError:
        return value


def _normalize_net_quantity(value: Any) -> Any:
    """
    Normalize Gemini net quantity output to the deterministic shape.

    Examples:
        "340 ml" -> {"amount": 340.0, "unit": "ml"}
        "1 kg"   -> {"amount": 1.0, "unit": "kg"}
        "1 Pair" -> {"amount": 1.0, "unit": "pair"}
    """
    if value is None:
        return None

    if isinstance(value, dict):
        if "amount" in value and "unit" in value:
            try:
                return {
                    "amount": float(value["amount"]),
                    "unit": str(value["unit"]).strip(),
                }
            except (TypeError, ValueError):
                return value
        return value

    text = str(value).strip()

    match = re.search(
        r"(?P<amount>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>kg|g|mg|l|ml|cl|dl|pair(?:s)?|unit(?:s)?|"
        r"number|nos?|pcs?|pieces?)\b",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return value

    try:
        amount = float(match.group("amount"))
    except ValueError:
        return value

    unit = match.group("unit").lower()

    unit_map = {
        "pairs": "pair",
        "units": "unit",
        "nos": "no",
        "pcs": "pc",
        "pieces": "piece",
    }

    unit = unit_map.get(unit, unit)

    return {
        "amount": amount,
        "unit": unit,
    }


def _normalize_manufacturing_date(value: Any) -> Any:
    """
    Normalize common Gemini manufacturing-date formats to the
    deterministic ISO-like representation used by Field Mapping.

    Examples:
        "12/2025"   -> "2025-12"
        "01/25"     -> "2025-01"
        "DEC.25"    -> "2025-12"
        "05/07/2026" -> "2026-07-05"
    """
    if value is None:
        return None

    text = str(value).strip().upper()

    month_names = {
        "JAN": 1,
        "JANUARY": 1,
        "FEB": 2,
        "FEBRUARY": 2,
        "MAR": 3,
        "MARCH": 3,
        "APR": 4,
        "APRIL": 4,
        "MAY": 5,
        "JUN": 6,
        "JUNE": 6,
        "JUL": 7,
        "JULY": 7,
        "AUG": 8,
        "AUGUST": 8,
        "SEP": 9,
        "SEPT": 9,
        "SEPTEMBER": 9,
        "OCT": 10,
        "OCTOBER": 10,
        "NOV": 11,
        "NOVEMBER": 11,
        "DEC": 12,
        "DECEMBER": 12,
    }

    text = text.replace(".", "").replace("-", "/")

    # Full numeric date: DD/MM/YYYY
    match = re.fullmatch(
        r"(\d{1,2})/(\d{1,2})/(\d{4})",
        text,
    )
    if match:
        day, month, year = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    # Month/year: MM/YYYY or MM/YY
    match = re.fullmatch(
        r"(\d{1,2})/(\d{2,4})",
        text,
    )
    if match:
        month, year = match.groups()

        month_num = int(month)
        year_num = int(year)

        if year_num < 100:
            year_num += 2000

        if 1 <= month_num <= 12:
            return f"{year_num:04d}-{month_num:02d}"

    # Text month + year: DEC/25, MAY/2025, DEC 25
    match = re.fullmatch(
        r"([A-Z]+)[/\s]+(\d{2,4})",
        text,
    )
    if match:
        month_text, year = match.groups()
        month_num = month_names.get(month_text)

        if month_num is not None:
            year_num = int(year)
            if year_num < 100:
                year_num += 2000

            return f"{year_num:04d}-{month_num:02d}"

    return value


def _normalize_consumer_care(value: Any) -> Any:
    """
    Normalize Gemini consumer-care output to the deterministic
    Field Mapping shape.

    The deterministic mapper uses:
        {"phone": ..., "email": ...}
    """
    if value is None:
        return None

    if isinstance(value, dict):
        return {
            "phone": value.get("phone"),
            "email": value.get("email"),
        }

    text = str(value).strip()

    phone_match = re.search(
        r"(?:\+91[\s-]?)?"
        r"(?:\(?\d{2,4}\)?[\s-]?)?"
        r"\d{3,5}[\s-]?\d{3,5}[\s-]?\d{3,5}",
        text,
    )

    email_match = re.search(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        text,
        flags=re.IGNORECASE,
    )

    phone = phone_match.group(0).strip() if phone_match else None

    # Preserve a leading Indian trunk prefix when Gemini returns one.
    if phone and re.search(r"(?:^|[\s(])1[-\s]?[789]", text) and not phone.startswith("1-"):
        phone = "1-" + phone
    email = email_match.group(0).strip() if email_match else None

    return {
        "phone": phone,
        "email": email,
    }


def _normalize_gemini_value(
    field_name: str,
    value: Any,
) -> Any:
    """
    Convert Gemini output into the same normalized value shape
    used by deterministic Field Mapping.
    """
    if field_name == "MRP":
        return _normalize_mrp(value)

    if field_name == "NET_QUANTITY":
        return _normalize_net_quantity(value)

    if field_name == "MANUFACTURING_DATE":
        return _normalize_manufacturing_date(value)

    if field_name == "CONSUMER_CARE":
        return _normalize_consumer_care(value)

    if field_name == "MANUFACTURER_ADDRESS":
        if value is None:
            return None
        return str(value).strip()

    return value


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

        normalized_value = _normalize_gemini_value(
            field_name,
            gemini_value,
        )

        if normalized_value is None:
            continue

        existing = merged.get(field_name)

        if not isinstance(existing, dict):
            continue

        existing_confidence = existing.get("confidence", "none")

        # Never overwrite a strong deterministic extraction. Gemini may
        # replace only missing/low-confidence deterministic results.
        if existing.get("value") is not None and existing_confidence not in {"none", "low"}:
            continue

        existing["value"] = normalized_value
        existing["confidence"] = "high"
        existing["method"] = "llm"
        existing["raw_evidence"] = gemini_value
        existing["all_candidates"] = [
            {
                "field": field_name,
                "value": normalized_value,
                "label_matched": "Gemini fallback",
                "score": 0.8,
                "span": [0, 0],
                "reasons": [
                    "deterministic Field Mapping did not resolve "
                    "the field",
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
        7. Normalize and fill only fields regex failed to resolve.
        8. On any Gemini failure, preserve the deterministic result.

    Gemini is therefore an optional enhancement and never a hard
    dependency for the pipeline.
    """
    mapping_result = map_fields(ocr_input)

    missing_fields = _missing_fields(mapping_result)

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
