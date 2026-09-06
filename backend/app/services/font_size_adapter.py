"""
backend/app/services/font_size_adapter.py
Adapter between router.py's font-check endpoint and check_font_size().
Reuses field_mapping_adapter.build_field_mapping_output() rather than
re-deriving field-mapping shape -- see its _FIELD_KEY_MAP for the
uppercase-name -> snake_case-key mapping this relies on.

Confirmed shapes (from field_mapping.py / field_mapping_adapter.py):
- mapping_output.fields is keyed by snake_case names (e.g. "net_quantity"),
  NOT direct attributes on mapping_output itself.
- ExtractedField.raw_value: plain string, e.g. "Net Qty 500 g".
- ExtractedField.normalized_value: structured data -- for net_quantity,
  a dict {"amount": float, "unit": str}, e.g. {"amount": 500.0, "unit": "g"}.

NOTE (perf/cost change): this now accepts an already-computed
mapping_result_dict instead of raw ocr_tokens, so callers that already
ran map_fields_with_fallback() at scan-init time (see router.py's
init_scan) don't pay for a second, redundant Gemini call + full regex
sweep just to get bboxes for the font-size check. This function is
synchronous again as a result -- it no longer awaits anything itself.

NOTE (multi-image scoping): mapping_result_dict may be a MERGED result
across multiple uploaded images (each field picks whichever image gave
the highest-confidence value, independently per field -- see router.py's
_merge_mapping_results). Font-size checking, however, only re-loads and
measures ONE physical image (image_path). If a field's winning value
came from a different image than the one being measured here, that
field's bbox refers to pixel coordinates on a DIFFERENT photo and must
not be drawn against this image. Callers MUST pre-scope
mapping_result_dict to the image actually being checked before calling
this function -- see field_mapping_fallback-tagged "_image_index" keys
and router.py's _scope_mapping_to_primary_image() helper, which is
responsible for stripping bboxes (not values) that don't belong to the
primary image before this function ever sees them. This module does not
re-derive that scoping itself, to avoid duplicating router.py's
knowledge of which image index is "primary."
"""
from typing import Any, Optional
import cv2
import numpy as np
from PIL import Image, ImageOps
from app.services.font_size import check_font_size
from app.services.field_mapping_adapter import build_field_mapping_output

_ATTR_TO_FIELD_NAME = {
    "mrp": "MRP",
    "net_quantity": "NET_QUANTITY",
    "manufacturer": "MANUFACTURER_ADDRESS",
    "mfg_date": "MANUFACTURING_DATE",
    "consumer_care": "CONSUMER_CARE",
}

# check_font_size()'s MIN_FONT_HEIGHT_MM assumes grams/ml -- convert other
# units to that base before passing net_quantity_g_or_ml through.
_UNIT_TO_GRAMS_OR_ML = {
    "g": 1.0, "gm": 1.0, "gram": 1.0, "grams": 1.0,
    "ml": 1.0, "millilitre": 1.0, "millilitres": 1.0,
    "kg": 1000.0, "l": 1000.0, "litre": 1000.0, "litres": 1000.0,
}

# Count-based units (e.g. "3 units", "6 pieces") have no direct
# weight/volume equivalent under the current MIN_FONT_HEIGHT_MM slabs,
# which are defined by net weight/volume per Legal Metrology (Packaged
# Commodities) Rules, 2011. Rather than fabricate a bogus gram-equivalent,
# these are tracked separately so callers/output can flag "not yet
# supported" instead of silently returning required_mm=None with no
# explanation.
_COUNT_BASED_UNITS = {
    "unit", "units", "piece", "pieces", "pcs", "pair", "pairs",
    "tablet", "tablets", "pill", "pills", "cigarette", "cigarettes",
    "stick", "sticks",
}


def _load_image_exif_corrected(path: str):
    pil_img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def try_check_font_size(
    image_path: str,
    mapping_result_dict: dict[str, Any],
    tap_point: tuple[int, int],
    coin_key: str = "5_rupee",
    net_quantity_g_or_ml: Optional[float] = None,
) -> Optional[dict]:
    try:
        image_bgr = _load_image_exif_corrected(image_path)
    except (FileNotFoundError, OSError):
        return None

    mapping_output = build_field_mapping_output(mapping_result_dict)

    labeled_tokens = []
    for attr_name, field_name in _ATTR_TO_FIELD_NAME.items():
        extracted = mapping_output.fields.get(attr_name)
        if extracted is None or extracted.bbox is None:
            continue
        bbox = extracted.bbox
        labeled_tokens.append({
            "text": extracted.raw_value,
            "bbox": (bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax),
            "field": field_name,
        })

    net_quantity_unit_supported = True
    if net_quantity_g_or_ml is None:
        nq = mapping_output.fields.get("net_quantity")
        if nq is not None and isinstance(nq.normalized_value, dict):
            amount = nq.normalized_value.get("amount")
            unit = (nq.normalized_value.get("unit") or "").lower()
            if unit in _COUNT_BASED_UNITS:
                net_quantity_unit_supported = False
            else:
                multiplier = _UNIT_TO_GRAMS_OR_ML.get(unit)
                if amount is not None and multiplier is not None:
                    net_quantity_g_or_ml = float(amount) * multiplier
                elif unit:
                    net_quantity_unit_supported = False

    try:
        result = check_font_size(
            ocr_tokens=labeled_tokens,
            image_dimensions=(image_bgr.shape[1], image_bgr.shape[0]),
            image_bgr=image_bgr,
            tap_point=tap_point,
            coin_key=coin_key,
            net_quantity_g_or_ml=net_quantity_g_or_ml,
        )
    except ValueError:
        return None

    if result is not None and not net_quantity_unit_supported:
        result["net_quantity_unit_supported"] = False
        result["note"] = (
            "Net quantity unit is count-based or unrecognized; "
            "font-size minimum could not be determined for this product."
        )
    return result