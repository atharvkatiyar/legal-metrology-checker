# app/services/field_mapping_fallback.py
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

# =====================================================================
# 1. ROBUST OFFLINE EDGE PARSER (Handles Indian FMCG Edge Cases)
# =====================================================================

PATTERNS = {
    # Highly greedy MRP pattern, ignores noise between currency symbol and number
    "MRP": re.compile(r'(?:RS|INR|₹|MRP|USP|PRICE)[^\d]*?([\d,]+(?:\.\d{1,2})?)', re.IGNORECASE),
    "NET_QUANTITY": re.compile(r'(\d+(?:\.\d+)?)\s*(g|kg|ml|l|liter|litre|pieces|units)\b', re.IGNORECASE),
    "DATE_STRICT": re.compile(r'(?<!\d)(\d{2})\s*[\./-]\s*(\d{2}|[A-Z]{3})\s*[\./-]\s*(\d{2,4})(?!\d)', re.IGNORECASE),
    "DATE_MONTH_YEAR": re.compile(r'\b(?:M|U|MFD\.?|EXP\.?|USE BY\.?|MFO:)?\s*(\d{2}|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*[\./-]?\s*(\d{2,4})\b', re.IGNORECASE),
    "CONSUMER_CARE": re.compile(r'(?i)([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|\b1800[- \s]?\d{3}[- \s]?\d{3,4}|\b\d{3,4}[- \s]?\d{6,8}\b)'),
    # Unbounded address capture to handle massive multi-line addresses (like Vicks)
    "MANUFACTURER_ADDRESS": re.compile(r'(?:MANUFACTURED|MARKETED|PACKED|MFD\.?|MKTD\.?)[\s\.]*BY[\s:,\-]+(.*?)(?=\b(?:FSSAI|LIC\b|CONSUMER|CUSTOMER|NET\b|MRP|BATCH|STORE|ALLERGEN|FOR\s|ISO\b|@|1800)|$)', re.IGNORECASE | re.DOTALL)
}

ANCHORS = {
    "MRP": [r'MRP', r'MAX.*RETAIL', r'PRICE', r'INCL.*TAXES', r'USP'],
    "MANUFACTURING_DATE": [r'MFD', r'MFG', r'MANUFACTURED', r'MFO', r'^M\s+', r'BATCH NO'],
    "EXPIRY_DATE": [r'EXP', r'USE BY', r'BEST BEFORE', r'^U\s+'],
    "NET_QUANTITY": [r'NET QTY', r'NET WT', r'WEIGHT', r'VOLUME', r'CONTENT']
}


def _get_bbox_bounds(token: Dict) -> Optional[List[float]]:
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
    return ((cx2 - cx1)**2 + (cy2 - cy1)**2)**0.5


def _robust_offline_mapping(tokens: List[Dict], full_text: str) -> Dict[str, Any]:
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
                        results[field_key] = {
                            "value": val_match.group(0).strip(),
                            "bbox": nearby_token.get("bbox"),
                            "confidence": "high",
                            "method": "regex",
                            "raw_evidence": f"Anchor [{text}] matched with [{nearby_text}]"
                        }
                        break
            if field_key in results:
                break
            
    # PASS 2: Full-Text Mega Sweep
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
                "confidence": "high",
                "method": "regex",
                "raw_evidence": best_match.group(0)
            }

    if "NET_QUANTITY" not in results:
        match = PATTERNS["NET_QUANTITY"].search(full_text)
        if match:
            results["NET_QUANTITY"] = {"value": match.group(0), "confidence": "high", "method": "regex", "raw_evidence": match.group(0)}
            
    if "MANUFACTURING_DATE" not in results:
        match = PATTERNS["DATE_STRICT"].search(full_text) or PATTERNS["DATE_MONTH_YEAR"].search(full_text)
        if match:
            results["MANUFACTURING_DATE"] = {"value": match.group(0), "confidence": "high", "method": "regex", "raw_evidence": match.group(0)}
            
    if "CONSUMER_CARE" not in results:
        match = PATTERNS["CONSUMER_CARE"].search(full_text)
        if match:
            results["CONSUMER_CARE"] = {"value": match.group(0), "confidence": "high", "method": "regex", "raw_evidence": match.group(0)}

    if "MANUFACTURER_ADDRESS" not in results:
        match = PATTERNS["MANUFACTURER_ADDRESS"].search(full_text)
        if match:
            val = match.group(1).strip() if match.groups() else match.group(0).strip()
            if len(val) > 10:
                results["MANUFACTURER_ADDRESS"] = {
                    "value": val, 
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
    
    # GIGO FIX: If it's a raw string that doesn't contain digits, return None instead of the string
    match = re.search(r"[\d,]+(?:\.\d{1,2})?", text)
    if not match: 
        return None  # <--- Returns None on parse failure, preventing garbage injection
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None  # <--- Returns None on ValueError


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
    
    # GIGO GUARD: Reject 6-digit runs that are actually pincodes (e.g. "400710")
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
            
    return None  # <--- GIGO FIX: Returns None if no genuine date pattern is found


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
    
    # Extract tokens and build full text
    tokens = []
    if isinstance(ocr_input, dict) and "images" in ocr_input:
        for img in ocr_input["images"]:
            tokens.extend(img.get("tokens", []))
    elif isinstance(ocr_input, list):
        tokens = ocr_input
        
    full_text = " ".join([t.get("text", "") for t in tokens if isinstance(t, dict) and "text" in t])
    
    robust_results = _robust_offline_mapping(tokens, full_text)
    
    for f in SUPPORTED_FIELDS:
        if f not in mapping_result:
            mapping_result[f] = {"value": None, "confidence": "none"}
            
    # Merge Robust Results (both low and high confidence, if missing from baseline)
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