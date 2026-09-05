"""
backend/app/services/test_real_ocr_e2e.py

Full real pipeline test: actual EasyOCR -> map_fields() -> inspect what
bbox shape MRP/NET_QUANTITY actually come back as, then try
try_check_font_size() against it for real.

Run: python3 test_real_ocr_e2e.py IMG_3478.jpeg
"""
import asyncio
import json
import sys

from app.services.ocr import extract_text_from_image
from app.field_mapping import map_fields
from app.services.font_size_adapter import try_check_font_size


async def main(image_path: str):
    print("Running real OCR (this may take a moment on CPU)...")
    ocr_tokens = await extract_text_from_image(image_path)
    print(f"\nOCR produced {len(ocr_tokens)} tokens. All of them:")
    for t in ocr_tokens:
        print(f"  {t}")

    mapping_result = map_fields(ocr_tokens)
    print("\nmap_fields() output for MRP and NET_QUANTITY:")
    print(json.dumps(mapping_result.get("MRP"), indent=2, default=str))
    print(json.dumps(mapping_result.get("NET_QUANTITY"), indent=2, default=str))

    print("\nNow trying try_check_font_size() against this real data...")
    result = try_check_font_size(
        image_path=image_path,
        ocr_tokens=ocr_tokens,
        tap_point=(1266, 342),
        coin_key="5_rupee",
    )
    print("\ntry_check_font_size() result:")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
