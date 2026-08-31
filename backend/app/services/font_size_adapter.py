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
"""
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.services.font_size import check_font_size
from app.field_mapping import map_fields
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


def _load_image_exif_corrected(path: str):
    pil_img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def try_check_font_size(
    image_path: str,
    ocr_tokens: list[dict],
    tap_point: tuple[int, int],
    coin_key: str = "5_rupee",
    net_quantity_g_or_ml: Optional[float] = None,
) -> Optional[dict]:
    try:
        image_bgr = _load_image_exif_corrected(image_path)
    except (FileNotFoundError, OSError):
        return None

    mapping_result_dict = map_fields(ocr_tokens)
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

    if net_quantity_g_or_ml is None:
        nq = mapping_output.fields.get("net_quantity")
        if nq is not None and isinstance(nq.normalized_value, dict):
            amount = nq.normalized_value.get("amount")
            unit = (nq.normalized_value.get("unit") or "").lower()
            multiplier = _UNIT_TO_GRAMS_OR_ML.get(unit)
            if amount is not None and multiplier is not None:
                net_quantity_g_or_ml = float(amount) * multiplier

    try:
        return check_font_size(
            ocr_tokens=labeled_tokens,
            image_dimensions=(image_bgr.shape[1], image_bgr.shape[0]),
            image_bgr=image_bgr,
            tap_point=tap_point,
            coin_key=coin_key,
            net_quantity_g_or_ml=net_quantity_g_or_ml,
        )
    except ValueError:
        return None
