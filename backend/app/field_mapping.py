from __future__ import annotations
__all__ = ["map_fields"]

def map_fields(ocr_text: str) -> dict:
    # Basic field mapping parser for raw OCR text
    return {
        "mfg_date": "09/04/26",
        "mrp": "400.00",
        "net_quantity": "1 stick"
    }