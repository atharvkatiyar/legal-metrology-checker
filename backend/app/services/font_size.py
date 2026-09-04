"""
backend/app/services/font_size.py — Font-Size & Readability Analyst module.

STATUS (Aug 29): working prototype, coin-based calibration.
Accuracy: ~10% mean error, n=2 labeled samples.
Coin detection: Hough Circle Transform (edge-based).

NOTE FOR INTEGRATION LEAD:
check_font_size() requires `image_bgr` (the actual image array) in addition
to ocr_tokens/image_dimensions — coin-based calibration needs real pixel
data to find and measure the reference coin, which token bboxes and
dimensions alone can't provide. This deviates from the resolve_* functions'
pure-token signature; the router will need to pass the loaded image through
to this call, not just OCR output.

TODO before submission:
- MIN_FONT_HEIGHT_MM is a PLACEHOLDER — get real slabs from Data & Rules Lead.
- Only 2 labeled samples tested so far.
- Assumes upright horizontal text; rotated label text not yet handled.
- Assumes a coin is present in the photo (approach tradeoff, not a bug).
- tap_point is currently a required manual input (from a user tap in the
  capture UI) — there's no automatic coin-location step yet.
"""

import cv2
import numpy as np


COIN_DIAMETERS_MM = {
    "1_rupee": 21.93,
    "2_rupee": 27.00,
    "5_rupee": 23.00,
    "10_rupee": 27.00,
}

# PLACEHOLDER — replace with real Legal Metrology (Packaged Commodities)
# Rules, 2011 font-height slabs from Data & Rules Lead before submission.
MIN_FONT_HEIGHT_MM = [
    (0, 200, 1.0),
    (200, 1000, 2.0),
    (1000, float("inf"), 4.0),
]


def _get_min_font_mm(net_quantity_g_or_ml: float) -> float:
    for lo, hi, mm in MIN_FONT_HEIGHT_MM:
        if lo <= net_quantity_g_or_ml < hi:
            return mm
    return MIN_FONT_HEIGHT_MM[-1][2]


def _detect_coin_scale(image_bgr, tap_point, coin_key="5_rupee", search_radius_px=450):
    """Returns mm_per_px using Hough Circle detection around tap_point."""
    x, y = tap_point
    h, w = image_bgr.shape[:2]
    x0, x1 = max(0, x - search_radius_px), min(w, x + search_radius_px)
    y0, y1 = max(0, y - search_radius_px), min(h, y + search_radius_px)
    crop = image_bgr[y0:y1, x0:x1]

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)

    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=search_radius_px,
        param1=100, param2=40, minRadius=search_radius_px // 6, maxRadius=search_radius_px,
    )
    if circles is None:
        raise ValueError("No coin circle found — check tap_point or search_radius_px.")

    seed = (x - x0, y - y0)
    best = min(circles[0], key=lambda c: (c[0] - seed[0]) ** 2 + (c[1] - seed[1]) ** 2)
    _, _, r_px = best

    return COIN_DIAMETERS_MM[coin_key] / (2 * r_px)


def check_font_size(
    ocr_tokens: list[dict],
    image_dimensions: tuple[int, int],
    image_bgr,
    tap_point: tuple[int, int],
    coin_key: str = "5_rupee",
    net_quantity_g_or_ml: float = None,
) -> dict:
    """
    ocr_tokens: OCR engine's token list — each token expected to have at
        least {"text": str, "bbox": (x1,y1,x2,y2), "field": str or None}.
        Only tokens with a recognized `field` (e.g. "MRP", "NET_QUANTITY")
        are checked for font-size compliance.
    image_dimensions: (width, height) — currently unused by calibration
        itself, kept for interface consistency with other resolve_* calls.
    image_bgr: the actual loaded image (OpenCV BGR array) — REQUIRED for
        coin detection. See module docstring note above.
    tap_point: (x, y) pixel coords of the coin's approx center — currently
        a manual input, no auto-detection step yet.
    net_quantity_g_or_ml: needed to pick the right MLR threshold slab. If
        None, tries to find a NET_QUANTITY token in ocr_tokens and parse it.

    Returns an ExtendedFieldResult-shaped dict:
        {"field": "FONT_SIZE", "value": {...}, "confidence": "high"/"low",
         "bbox": [...], "violations": [...]}
    """
    mm_per_px = _detect_coin_scale(image_bgr, tap_point, coin_key)

    if net_quantity_g_or_ml is None:
        for tok in ocr_tokens:
            if tok.get("field") == "NET_QUANTITY":
                try:
                    net_quantity_g_or_ml = float("".join(
                        ch for ch in tok["text"] if ch.isdigit() or ch == "."))
                except (ValueError, KeyError):
                    pass
                break

    min_required = (_get_min_font_mm(net_quantity_g_or_ml)
                     if net_quantity_g_or_ml is not None else None)

    violations = []
    checked_fields = []
    for tok in ocr_tokens:
        field = tok.get("field")
        bbox = tok.get("bbox")
        if not field or not bbox:
            continue
        x1, y1, x2, y2 = bbox
        height_mm = (y2 - y1) * mm_per_px
        entry = {
            "field": field,
            "bbox": list(bbox),
            "measured_mm": round(height_mm, 3),
            "required_mm": min_required,
        }
        checked_fields.append(entry)
        if min_required is not None and height_mm < min_required:
            violations.append({
                **entry,
                "violation": "FONT_SIZE_BELOW_MINIMUM",
            })

    return {
        "field": "FONT_SIZE",
        "value": {"mm_per_px": mm_per_px, "checked_fields": checked_fields},
        "confidence": "low" if min_required is None else "high",
        "bbox": None,
        "violations": violations,
    }
