"""
backend/app/services/test_adapter_e2e.py

Tests try_check_font_size() (the adapter) end-to-end, using synthetic
"raw OCR" tokens standing in for real extract_text_from_image() output.

ASSUMPTION FLAGGED: raw OCR token shape is guessed as
{"text": str, "bbox": [x1, y1, x2, y2]} -- matching the shape
check_font_size() itself expects, since ocr.py's actual output format
hasn't been confirmed. If map_fields() expects something different
(different key names, a different bbox format, etc.), this will likely
fail at map_fields() or silently produce empty field_mapping results --
either outcome tells us something real about the mismatch.

Run: python3 test_adapter_e2e.py IMG_3478.jpeg
"""
import sys
from app.services.font_size_adapter import try_check_font_size

if __name__ == "__main__":
    image_path = sys.argv[1]

    # Synthetic raw OCR tokens -- real text/bboxes from IMG_3478, in a
    # guessed "raw OCR" shape (no "field" label -- that's map_fields()'s job).
    fake_raw_ocr_tokens = [
        {"text": "M.R.P.(Incl. of all taxes):", "bbox": [122, 1650, 700, 1720]},
        {"text": "Rs.130.00", "bbox": [1282, 1896, 1675, 1989]},
        {"text": "Net Quantity", "bbox": [122, 2500, 500, 2560]},
        {"text": "3 N", "bbox": [700, 2500, 900, 2560]},
    ]

    result = try_check_font_size(
        image_path=image_path,
        ocr_tokens=fake_raw_ocr_tokens,
        tap_point=(1266, 342),
        coin_key="5_rupee",
        net_quantity_g_or_ml=None,  # let it try to derive from mapping
    )

    if result is None:
        print("try_check_font_size() returned None -- either no coin found, "
              "image couldn't load, or field mapping produced nothing usable.")
    else:
        import json
        print("try_check_font_size() ran. Output:")
        print(json.dumps(result, indent=2, default=str))
