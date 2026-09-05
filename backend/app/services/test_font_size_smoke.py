"""
backend/app/services/test_font_size_smoke.py

Minimal smoke test for check_font_size() — proves the packaged version
runs end-to-end (same logic as the standalone prototype, now via the
router-facing entry point) without crashing on realistic input.

Run: python3 test_font_size_smoke.py path/to/photo.jpeg

This does NOT replace run_accuracy_test() from the prototype — it's just
confirming the packaged interface works, not measuring accuracy.
"""
import sys
import cv2
import numpy as np
from PIL import Image, ImageOps

sys.path.insert(0, ".")  # adjust if running from a different cwd
from font_size import check_font_size


def load_image_exif_corrected(path):
    pil_img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


if __name__ == "__main__":
    image_path = sys.argv[1]
    img = load_image_exif_corrected(image_path)
    h, w = img.shape[:2]

    # Fake ocr_tokens — replace with real OCR Engineer output once available.
    # Uses the same known-good coordinates format we validated yesterday.
    fake_ocr_tokens = [
        {"text": "130.00", "bbox": (1282, 1896, 1675, 1989), "field": "MRP"},
        {"text": "3 N", "bbox": (1282, 1896, 1675, 1989), "field": "NET_QUANTITY"},
    ]

    result = check_font_size(
        ocr_tokens=fake_ocr_tokens,
        image_dimensions=(w, h),
        image_bgr=img,
        tap_point=(1266, 342),   # coin center — from picker.py, image-specific
        coin_key="5_rupee",
        net_quantity_g_or_ml=200,  # placeholder, real value should come from field_mapping
    )

    print("check_font_size() ran without crashing. Output:")
    import json
    print(json.dumps(result, indent=2, default=str))

    assert result["field"] == "FONT_SIZE"
    assert "violations" in result
    assert "value" in result
    print("\nSmoke test PASSED — output shape matches expected ExtendedFieldResult.")
