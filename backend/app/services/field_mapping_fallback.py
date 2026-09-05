# app/services/field_mapping_fallback.py
from __future__ import annotations
import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

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

# =====================================================================
# 1. ROBUST OFFLINE EDGE PARSER (Handles Indian FMCG Edge Cases)
# =====================================================================

PATTERNS = {
    "MRP": re.compile(r'(?:RS|INR|₹|MRP|USP|PRICE)[^\d]*?([\d,]+(?:\.\d{1,2})?)', re.IGNORECASE),
    "NET_QUANTITY": re.compile(r'(\d+(?:\.\d+)?)\s*(g|kg|ml|l|liter|litre|pieces|units)\b', re.IGNORECASE),
    "DATE_STRICT": re.compile(r'(?<!\d)(\d{2})\s*[\./-]\s*(\d{2}|[A-Z]{3})\s*[\./-]\s*(\d{2,4})(?!\d)', re.IGNORECASE),
    "DATE_MONTH_YEAR": re.compile(r'\b(?:M|U|MFD\.?|EXP\.?|USE BY\.?|MFO:)?\s*(\d{2}|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*[\./-]?\s*(\d{2,4})\b', re.IGNORECASE),
    "CONSUMER_CARE": re.compile(r'(?i)([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|\b1800[- \s]?\d{3}[- \s]?\d{3,4}|\b\d{3,4}[- \s]?\d{6,8}\b)'),
    "MANUFACTURER_ADDRESS": re.compile(r'(?:MANUFACTURED|MARKETED|PACKED|MFD\.?|MKTD\.?)[\s\.]*BY[\s:,\-]+(.*?)(?=\b(?:FSSAI|LIC\b|CONSUMER|CUSTOMER|NET\b|MRP|BATCH|STORE|ALLERGEN|FOR\s|ISO\b|@|1800)|$)', re.IGNORECASE | re.DOTALL)
}

ANCHORS = {
    "MRP": [r'MRP', r'MAX.*RETAIL', r'PRICE', r'INCL.*TAXES', r'USP'],
    "MANUFACTURING_DATE": [r'MFD', r'MFG', r'MANUFACTURED', r'MFO', r'^M\s+', r'BATCH NO'],
    "EXPIRY_DATE": [r'EXP', r'USE BY', r'BEST BEFORE', r'^U\s+'],
    "NET_QUANTITY": [r'NET QTY', r'NET WT', r'WEIGHT', r'VOLUME', r'CONTENT']
}


def _get_bbox_bounds(token: Dict) -> Optional[List[float]]:
    """Returns flat [xmin, ymin, xmax, ymax] bounds for a single OCR
    token, accepting either a 4-corner point-list bbox or an already-flat
    4-number bbox on the token."""
    bbox = token.get("bbox") or token.get("box")
    if not bbox:
        return None
    if isinstance(bbox, list) and len(bbox) == 4 and isinstance(bbox[0], list):
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        return [min(xs), min(ys), max(xs), max(ys)]
    if isinstance(bbox, list) and len(bbox) == 4:
        return bbox
    return None


def _bounds_to_point_box(bounds: List[float]) -> List[List[float]]:
    """Converts flat [xmin, ymin, xmax, ymax] bounds into a single
    4-corner polygon [[x,y],[x,y],[x,y],[x,y]].

    IMPORTANT: this is ONE polygon, not the "list of polygons" shape
    `field_mapping_adapter._polygons_to_bbox` expects as its `bbox`
    input. Every call site below wraps this in an outer list -- e.g.
    `[_bounds_to_point_box(bounds)]` -- before assigning it to a
    result's "bbox" key. Passing the bare polygon (unwrapped) is exactly
    the bug this fix corrects: `_polygons_to_bbox` iterates
    `for polygon in polygons: for point in polygon`, so an unwrapped
    single polygon gets its own [x,y] corners misread as "polygons" and
    their scalar x/y values misread as "points", silently producing an
    empty box (None) with no error raised."""
    x_min, y_min, x_max, y_max = bounds
    return [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]


def _is_in_directional_window(
    anchor_box: List[float],
    target_box: List[float],
    max_right: int = 400,
    max_down: int = 60,
    max_up: int = 40
) -> bool:
    a_x_min, a_y_min, a_x_max, a_y_max = anchor_box
    t_x_min, t_y_min, t_x_max, t_y_max = target_box
    if t_x_min < a_x_min - 40: return False
    if t_x_min > a_x_max + max_right: return False
    if t_y_min < a_y_min - max_up or t_y_max > a_y_max + max_down: return False
    return True


def _calculate_distance(box1: List[float], box2: List[float]) -> float:
    cx1, cy1 = (box1[0] + box1[2]) / 2, (box1[1] + box1[3]) / 2
    cx2, cy2 = (box2[0] + box2[2]) / 2, (box2[1] + box2[3]) / 2
    return ((cx2 - cx1) ** 2 + (cy2 - cy1) ** 2) ** 0.5


def _token_bbox_as_polygon_list(token: Dict) -> Optional[List[List[List[float]]]]:
    """Wraps a single token's bbox into the `[polygon]` shape
    `_polygons_to_bbox` expects (a list containing one 4-corner
    polygon). Returns None if the token has no usable bbox, so callers
    can assign this directly to a result dict's "bbox" key without an
    extra None-check at each call site."""
    bounds = _get_bbox_bounds(token)
    if not bounds:
        return None
    return [_bounds_to_point_box(bounds)]


# ---------------------------------------------------------------------------
# Token-span provenance for the Pass 2 full-text mega sweep.
# ---------------------------------------------------------------------------
# full_text is built by joining each qualifying token's text with a single
# space. This builds that exact same string AND a parallel list of
# (start, end, token) spans in one pass, so a regex match's character
# offsets in full_text can always be traced back to the token(s) that
# produced that span -- without re-deriving the join logic (and risking
# it drifting out of sync) at the call site.

def _build_full_text_with_spans(
    tokens: List[Dict[str, Any]]
) -> Tuple[str, List[Dict[str, Any]]]:
    spans: List[Dict[str, Any]] = []
    parts: List[str] = []
    cursor = 0
    for tok in tokens:
        if not isinstance(tok, dict) or "text" not in tok:
            continue
        text = tok.get("text", "")
        if parts:
            cursor += 1  # the single space that will join this token on
        start = cursor
        end = start + len(text)
        spans.append({"start": start, "end": end, "token": tok})
        parts.append(text)
        cursor = end
    full_text = " ".join(parts)
    return full_text, spans


def _get_matched_tokens_bbox(
    token_spans: List[Dict[str, Any]], start_idx: int, end_idx: int
) -> Optional[List[List[List[float]]]]:
    """
    Maps a full_text character span [start_idx, end_idx) back to the
    original OCR tokens (via the spans built by
    _build_full_text_with_spans, so offsets can never drift out of sync
    with how full_text was actually assembled) and returns the list of
    each supporting token's bbox, each converted to a 4-corner polygon.

    Returns a LIST of polygons (one per supporting token) -- the exact
    shape `field_mapping_adapter._polygons_to_bbox` expects as input,
    which internally collapses however many polygons it's given into one
    enclosing box. This deliberately does NOT pre-collapse to a single
    box here, to avoid duplicating that aggregation logic in two places.

    Returns None -- not [] -- when no supporting token has a usable
    bbox, so callers can distinguish "not computed" from "computed as
    empty."
    """
    polygons: List[List[List[float]]] = []
    for span in token_spans:
        tok_start, tok_end, tok = span["start"], span["end"], span["token"]
        # Half-open interval overlap check.
        if tok_end <= start_idx or tok_start >= end_idx:
            continue
        bounds = _get_bbox_bounds(tok)
        if bounds:
            polygons.append(_bounds_to_point_box(bounds))

    if not polygons:
        return None
    return polygons


def _robust_offline_mapping(
    tokens: List[Dict],
    full_text: str,
    token_spans: List[Dict[str, Any]],
) -> Dict[str, Any]:
    results = {}
    valid_tokens = [t for t in tokens if isinstance(t, dict) and "text" in t and _get_bbox_bounds(t)]

    # PASS 1: Anchor-Based Directional Search
    for field_key, anchor_patterns in ANCHORS.items():
        regex_key = field_key if "DATE" not in field_key else "DATE_STRICT"

        for token in valid_tokens:
            text = token.get("text", "").upper()
            if any(re.search(p, text) for p in anchor_patterns):
                anchor_bbox = _get_bbox_bounds(token)

                nearby_tokens = [
                    t for t in valid_tokens
                    if t != token and _is_in_directional_window(anchor_bbox, _get_bbox_bounds(t))
                ]
                nearby_tokens.sort(key=lambda t: _calculate_distance(anchor_bbox, _get_bbox_bounds(t)))

                for nearby_token in nearby_tokens:
                    nearby_text = nearby_token.get("text", "")
                    val_match = PATTERNS[regex_key].search(nearby_text)
                    if not val_match and "DATE" in field_key:
                        val_match = PATTERNS["DATE_MONTH_YEAR"].search(nearby_text)

                    if val_match:
                        # FIX: was `nearby_token.get("bbox")` -- a bare
                        # single-token polygon that `_polygons_to_bbox`
                        # silently mis-parses into an empty box (see
                        # `_bounds_to_point_box` docstring above). Wrap
                        # it in the polygon-list shape it actually needs.
                        results[field_key] = {
                            "value": val_match.group(0).strip(),
                            "bbox": _token_bbox_as_polygon_list(nearby_token),
                            "confidence": "high",
                            "method": "regex",
                            "raw_evidence": f"Anchor [{text}] matched with [{nearby_text}]"
                        }
                        break
            if field_key in results:
                break

    # PASS 2: Full-Text Mega Sweep
    # Each branch below resolves a bbox by mapping the regex match's
    # character span back to its supporting token(s) via
    # _get_matched_tokens_bbox, instead of leaving "bbox" unset (which
    # previously left these fields with zero spatial data and blocked
    # the Rule 9 font-size validator downstream).

    if "MRP" not in results:
        matches = PATTERNS["MRP"].finditer(full_text)
        best_match = None
        max_val = -1.0

        for m in matches:
            try:
                v = float(m.group(1).replace(',', ''))
                if v > max_val:
                    max_val = v
                    best_match = m
            except ValueError:
                pass

        if best_match:
            results["MRP"] = {
                "value": best_match.group(0).strip(),
                "bbox": _get_matched_tokens_bbox(token_spans, best_match.start(1), best_match.end(1)),
                "confidence": "high",
                "method": "regex",
                "raw_evidence": best_match.group(0)
            }

    if "NET_QUANTITY" not in results:
        match = PATTERNS["NET_QUANTITY"].search(full_text)
        if match:
            results["NET_QUANTITY"] = {
                "value": match.group(0),
                "bbox": _get_matched_tokens_bbox(token_spans, match.start(0), match.end(0)),
                "confidence": "high",
                "method": "regex",
                "raw_evidence": match.group(0),
            }

    if "MANUFACTURING_DATE" not in results:
        match = PATTERNS["DATE_STRICT"].search(full_text) or PATTERNS["DATE_MONTH_YEAR"].search(full_text)
        if match:
            results["MANUFACTURING_DATE"] = {
                "value": match.group(0),
                "bbox": _get_matched_tokens_bbox(token_spans, match.start(0), match.end(0)),
                "confidence": "high",
                "method": "regex",
                "raw_evidence": match.group(0),
            }

    if "CONSUMER_CARE" not in results:
        match = PATTERNS["CONSUMER_CARE"].search(full_text)
        if match:
            results["CONSUMER_CARE"] = {
                "value": match.group(0),
                "bbox": _get_matched_tokens_bbox(token_spans, match.start(0), match.end(0)),
                "confidence": "high",
                "method": "regex",
                "raw_evidence": match.group(0),
            }

    if "MANUFACTURER_ADDRESS" not in results:
        match = PATTERNS["MANUFACTURER_ADDRESS"].search(full_text)
        if match:
            has_group = bool(match.groups())
            val = match.group(1).strip() if has_group else match.group(0).strip()
            if len(val) > 10:
                span_start, span_end = (match.start(1), match.end(1)) if has_group else (match.start(0), match.end(0))
                results["MANUFACTURER_ADDRESS"] = {
                    "value": val,
                    "bbox": _get_matched_tokens_bbox(token_spans, span_start, span_end),
                    "confidence": "high",
                    "method": "regex",
                    "raw_evidence": match.group(0)
                }

    return results


# =====================================================================
# 2. NORMALIZATION & GEMINI FALLBACK LOGIC (GIGO-Protected)
# =====================================================================

def _is_missing_regex_result(field_result: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(field_result, dict):
        return True
    value = field_result.get("value")
    if value is None:
        return True
    return field_result.get("confidence", "none") in {"none", "low"}


def _missing_fields(mapping_result: Dict[str, Any]) -> List[str]:
    return [
        field_name
        for field_name in SUPPORTED_FIELDS
        if _is_missing_regex_result(mapping_result.get(field_name))
    ]


def _normalize_mrp(value: Any) -> Any:
    if value is None: return None
    if isinstance(value, (int, float)): return float(value)
    text = str(value).strip()
    match = re.search(r"[\d,]+(?:\.\d{1,2})?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _normalize_net_quantity(value: Any) -> Any:
    if value is None: return None
    if isinstance(value, dict):
        if "amount" in value and "unit" in value:
            try: return {"amount": float(value["amount"]), "unit": str(value["unit"]).strip()}
            except (TypeError, ValueError): return None
        return None
    text = str(value).strip()
    match = re.search(
        r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|mg|l|ml|cl|dl|pair(?:s)?|unit(?:s)?|number|nos?|pcs?|pieces?)\b",
        text, flags=re.IGNORECASE,
    )
    if not match: return None
    try: amount = float(match.group("amount"))
    except ValueError: return None
    unit = match.group("unit").lower()
    unit_map = {"pairs": "pair", "units": "unit", "nos": "no", "pcs": "pc", "pieces": "piece"}
    return {"amount": amount, "unit": unit_map.get(unit, unit)}


def _normalize_manufacturing_date(value: Any) -> Any:
    if value is None: return None
    text = str(value).strip().upper()
    if re.fullmatch(r"\d{6}", text):
        return None
    text = re.sub(r'[\s\.\-]+', '/', text)
    month_names = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12
    }
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", text)
    if match:
        day, month, year = match.groups()
        year_num = int(year)
        if year_num < 100: year_num += 2000
        return f"{year_num:04d}-{int(month):02d}-{int(day):02d}"
    match = re.search(r"\b(\d{1,2})/(\d{2,4})\b", text)
    if match:
        month, year = match.groups()
        month_num = int(month)
        year_num = int(year)
        if year_num < 100: year_num += 2000
        if 1 <= month_num <= 12: return f"{year_num:04d}-{month_num:02d}"
    match = re.search(r"\b([A-Z]{3,})/?(\d{2,4})\b", text)
    if match:
        month_text, year = match.groups()
        month_num = month_names.get(month_text[:3])
        if month_num is not None:
            year_num = int(year)
            if year_num < 100: year_num += 2000
            return f"{year_num:04d}-{month_num:02d}"
    return None


def _normalize_consumer_care(value: Any) -> Any:
    if value is None: return None
    if isinstance(value, dict): return {"phone": value.get("phone"), "email": value.get("email")}
    text = str(value).strip()
    phone_match = re.search(r"(?:\+91[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)?\d{3,5}[\s-]?\d{3,5}[\s-]?\d{3,5}", text)
    email_match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, flags=re.IGNORECASE)
    phone = phone_match.group(0).strip() if phone_match else None
    if phone and re.search(r"(?:^|[\s(])1[-\s]?[789]", text) and not phone.startswith("1-"):
        phone = "1-" + phone
    email = email_match.group(0).strip() if email_match else None
    return {"phone": phone, "email": email}


def _normalize_gemini_value(field_name: str, value: Any) -> Any:
    if field_name == "MRP": return _normalize_mrp(value)
    if field_name == "NET_QUANTITY": return _normalize_net_quantity(value)
    if field_name == "MANUFACTURING_DATE": return _normalize_manufacturing_date(value)
    if field_name == "CONSUMER_CARE": return _normalize_consumer_care(value)
    if field_name == "MANUFACTURER_ADDRESS": return str(value).strip() if value else None
    return value


def _merge_gemini_fallback(mapping_result: Dict[str, Any], gemini_result: Dict[str, Any], missing_fields: List[str]) -> Dict[str, Any]:
    merged = dict(mapping_result)
    for field_name in missing_fields:
        gemini_value = gemini_result.get(field_name)
        if gemini_value is None: continue
        normalized_value = _normalize_gemini_value(field_name, gemini_value)
        if normalized_value is None: continue
        existing = merged.get(field_name, {})
        if not isinstance(existing, dict): continue
        existing_confidence = existing.get("confidence", "none")
        if existing.get("value") is not None and existing_confidence not in {"none", "low"}:
            continue
        existing["value"] = normalized_value
        existing["confidence"] = "high"
        existing["method"] = "llm"
        existing["raw_evidence"] = gemini_value
        existing["all_candidates"] = [{
            "field": field_name,
            "value": normalized_value,
            "label_matched": "Gemini fallback",
            "score": 0.8,
            "span": [0, 0],
            "reasons": ["value supplied by Gemini fallback"]
        }]
        merged[field_name] = existing
    return merged


async def _call_gemini_with_timeout(image_path: str) -> Dict[str, Any]:
    return await asyncio.wait_for(
        asyncio.to_thread(extract_fields_from_image, image_path),
        timeout=GEMINI_TIMEOUT_SECONDS,
    )


# =====================================================================
# 3. MAIN ENTRY POINT ORCHESTRATOR
# =====================================================================

async def map_fields_with_fallback(ocr_input: Any, image_path: Optional[str] = None) -> Dict[str, Any]:
    mapping_result = map_fields(ocr_input)

    tokens = []
    if isinstance(ocr_input, dict) and "images" in ocr_input:
        for img in ocr_input["images"]:
            tokens.extend(img.get("tokens", []))
    elif isinstance(ocr_input, list):
        tokens = ocr_input

    # full_text and token_spans are now built together from a single
    # source of truth, so Pass 2's regex offsets can always be mapped
    # back to the tokens that produced them.
    full_text, token_spans = _build_full_text_with_spans(tokens)

    robust_results = _robust_offline_mapping(tokens, full_text, token_spans)

    for f in SUPPORTED_FIELDS:
        if f not in mapping_result:
            mapping_result[f] = {"value": None, "confidence": "none"}

    for key, robust_val in robust_results.items():
        is_missing_in_baseline = _is_missing_regex_result(mapping_result.get(key))
        is_high_conf = robust_val.get("confidence") == "high"

        if is_missing_in_baseline or is_high_conf:
            normalized_val = _normalize_gemini_value(key, robust_val["value"])
            if normalized_val is not None:
                robust_val["value"] = normalized_val
                mapping_result[key] = robust_val

    missing_fields = _missing_fields(mapping_result)

    if not missing_fields or not image_path:
        return mapping_result
    try:
        gemini_result = await _call_gemini_with_timeout(image_path)
    except asyncio.TimeoutError:
        logger.warning("Gemini fallback timed out. Returning deterministic Field Mapping result.")
        return mapping_result
    except Exception as e:
        logger.error(f"Gemini fallback failed ({e}). Returning deterministic Field Mapping result.")
        return mapping_result
    if not isinstance(gemini_result, dict):
        return mapping_result
    return _merge_gemini_fallback(mapping_result, gemini_result, missing_fields)


__all__ = [
    "SUPPORTED_FIELDS",
    "GEMINI_TIMEOUT_SECONDS",
    "map_fields_with_fallback",
]