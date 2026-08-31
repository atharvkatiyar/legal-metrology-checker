import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.services.ocr as ocr
from app.field_mapping import map_fields


def test_to_ocr_tokens():
    results = [
        (
            [[10, 20], [100, 20], [100, 50], [10, 50]],
            "MRP ₹249",
            0.96,
        ),
        (
            [[10, 60], [130, 60], [130, 90], [10, 90]],
            "मूल्य ₹249",
            0.91,
        ),
    ]

    tokens = ocr._to_ocr_tokens(results)

    assert len(tokens) == 2

    assert tokens[0]["text"] == "MRP ₹249"
    assert tokens[0]["bbox"] == [
        [10, 20],
        [100, 20],
        [100, 50],
        [10, 50],
    ]
    assert tokens[0]["confidence"] == 0.96
    assert tokens[0]["language"] == "en"

    assert tokens[1]["language"] == "hi"

    json.dumps(tokens)


def test_empty_results():
    assert ocr._to_ocr_tokens([]) == []


def test_malformed_results_are_skipped():
    results = [
        ("bad",),
        ("too", "many", "values", "here"),
    ]

    assert ocr._to_ocr_tokens(results) == []


def test_real_image_path():
    fixture = (
        Path(__file__).resolve().parents[2]
        / "labeled_batch"
        / "images"
        / "IMG_003 (Himalaya Shampoo).jpg"
    )

    assert fixture.exists(), f"Missing fixture: {fixture}"

    result = asyncio.run(
        ocr.extract_text_from_image(str(fixture))
    )

    assert isinstance(result, list)
    assert result, "Expected at least one OCR detection from the labeled image"

    first = result[0]

    assert isinstance(first["text"], str)
    assert isinstance(first["bbox"], list)
    assert isinstance(first["confidence"], float)
    assert first["language"] in {"en", "hi"}

    json.dumps(result)


def test_ocr_tokens_are_compatible_with_field_mapping():
    tokens = [
        {
            "text": "MRP ₹301",
            "bbox": [[10, 20], [100, 20], [100, 50], [10, 50]],
            "confidence": 0.96,
            "language": "en",
        },
        {
            "text": "Net Qty. 340 ml",
            "bbox": [[10, 60], [130, 60], [130, 90], [10, 90]],
            "confidence": 0.95,
            "language": "en",
        },
        {
            "text": "Manufactured by Himalaya Wellness Company",
            "bbox": [[10, 100], [300, 100], [300, 130], [10, 130]],
            "confidence": 0.94,
            "language": "en",
        },
        {
            "text": "Date of Manufacture 12/2025",
            "bbox": [[10, 140], [220, 140], [220, 170], [10, 170]],
            "confidence": 0.93,
            "language": "en",
        },
        {
            "text": "Customer Care 1-800-208-1930",
            "bbox": [[10, 180], [250, 180], [250, 210], [10, 210]],
            "confidence": 0.92,
            "language": "en",
        },
    ]

    result = map_fields(tokens)

    assert isinstance(result, dict)

    for key in (
        "MRP",
        "NET_QUANTITY",
        "MANUFACTURER_ADDRESS",
        "MANUFACTURING_DATE",
        "CONSUMER_CARE",
    ):
        assert key in result

    json.dumps(result)


if __name__ == "__main__":
    test_to_ocr_tokens()
    test_empty_results()
    test_malformed_results_are_skipped()
    test_real_image_path()
    test_ocr_tokens_are_compatible_with_field_mapping()
    print("OCR tests: PASS")
