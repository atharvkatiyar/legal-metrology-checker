import asyncio
import json
from pathlib import Path
from typing import Any

from app.services.ocr import extract_text_from_image
from app.field_mapping import map_fields


ROOT = Path(__file__).resolve().parents[2]
LABELS_PATH = ROOT / "labeled_batch" / "labels.json"
IMAGES_DIR = ROOT / "labeled_batch" / "images"

FIELDS = (
    "MRP",
    "NET_QUANTITY",
    "MANUFACTURER_ADDRESS",
    "MANUFACTURING_DATE",
    "CONSUMER_CARE",
)


def normalize_expected(field: str, value: Any) -> Any:
    """
    Normalize only the ground-truth representation enough for
    deterministic comparison with Field Mapping output.
    """
    if value is None:
        return None

    if field == "MRP":
        # Examples:
        # "Rs. 301.00" -> 301.0
        # "MRP ₹10.00" -> 10.0
        # "₹ 280.00" -> 280.0
        text = str(value)
        import re

        match = re.search(r"\d[\d,]*(?:\.\d+)?", text)
        if not match:
            return None

        raw = match.group(0).replace(",", "")
        return float(raw)

    if field == "NET_QUANTITY":
        text = str(value).lower().strip()

        # Normalize common unit spellings.
        text = text.replace("millilitres", "ml")
        text = text.replace("milliliters", "ml")
        text = text.replace("kilograms", "kg")
        text = text.replace("kilogram", "kg")
        text = text.replace("grams", "g")
        text = text.replace("gram", "g")
        text = text.replace("litres", "l")
        text = text.replace("liters", "l")

        # Handle values such as:
        # "340 ml"
        # "200ml"
        # "1.5 l"
        # "300ml (294.8g)"
        import re

        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(mg|kg|ml|g|l)\b",
            text,
        )
        if not match:
            return None

        return {
            "amount": float(match.group(1)),
            "unit": match.group(2),
        }

    if field == "CONSUMER_CARE":
        # Compare as normalized text for now.
        return " ".join(str(value).lower().split())

    if field in ("MANUFACTURER_ADDRESS", "MANUFACTURING_DATE"):
        return " ".join(str(value).lower().split())

    return value


def normalize_actual(field: str, result: dict[str, Any]) -> Any:
    value = result.get("value")

    if value is None:
        return None

    if field == "MRP":
        return float(value)

    if field == "NET_QUANTITY":
        if not isinstance(value, dict):
            return None

        return {
            "amount": float(value["amount"]),
            "unit": str(value["unit"]).lower(),
        }

    if field == "CONSUMER_CARE":
        if not isinstance(value, dict):
            return None

        phone = value.get("phone")
        email = value.get("email")

        parts = []
        if phone:
            parts.append(str(phone).lower())
        if email:
            parts.append(str(email).lower())

        return " / ".join(parts)

    if field in ("MANUFACTURER_ADDRESS", "MANUFACTURING_DATE"):
        return " ".join(str(value).lower().split())

    return value


async def evaluate_image(record: dict[str, Any]) -> dict[str, Any]:
    image_name = record["image"]
    image_path = IMAGES_DIR / image_name

    if not image_path.exists():
        raise FileNotFoundError(f"Missing image: {image_path}")

    ocr_tokens = await extract_text_from_image(str(image_path))
    mapped = map_fields(ocr_tokens)

    field_results = {}

    for field in FIELDS:
        expected = normalize_expected(field, record.get(field))
        actual = normalize_actual(field, mapped[field])

        field_results[field] = {
            "expected": expected,
            "actual": actual,
            "pass": expected == actual,
        }

    return {
        "image": image_name,
        "ocr_token_count": len(ocr_tokens),
        "fields": field_results,
    }


async def main() -> None:
    with open(LABELS_PATH, "r", encoding="utf-8") as f:
        labels = json.load(f)

    totals = {field: 0 for field in FIELDS}
    passes = {field: 0 for field in FIELDS}

    failures = []

    print("=" * 72)
    print("AUG 29 — LABELED BATCH FIELD MAPPING EVALUATION")
    print("=" * 72)
    print(f"Dataset size: {len(labels)} images")
    print()

    for record in labels:
        result = await evaluate_image(record)

        print(f"[IMAGE] {result['image']}")
        print(f"  OCR tokens: {result['ocr_token_count']}")

        for field in FIELDS:
            field_result = result["fields"][field]
            totals[field] += 1

            if field_result["pass"]:
                passes[field] += 1
                status = "PASS"
            else:
                status = "FAIL"
                failures.append(
                    {
                        "image": result["image"],
                        "field": field,
                        "expected": field_result["expected"],
                        "actual": field_result["actual"],
                    }
                )

            print(f"  [{status}] {field}")

        print()

    print("=" * 72)
    print("FIELD ACCURACY")
    print("=" * 72)

    for field in FIELDS:
        accuracy = (passes[field] / totals[field]) * 100 if totals[field] else 0
        print(
            f"{field:24s} "
            f"{passes[field]:2d}/{totals[field]:2d} "
            f"({accuracy:6.2f}%)"
        )

    total_pass = sum(passes.values())
    total_cases = sum(totals.values())
    overall_accuracy = (
        (total_pass / total_cases) * 100 if total_cases else 0
    )

    print("-" * 72)
    print(
        f"{'OVERALL':24s} "
        f"{total_pass:2d}/{total_cases:2d} "
        f"({overall_accuracy:6.2f}%)"
    )

    print()
    print("=" * 72)
    print("FAILURE PATTERNS")
    print("=" * 72)

    if not failures:
        print("No failures.")
    else:
        for failure in failures:
            print(f"[FAIL] {failure['image']}")
            print(f"  Field:    {failure['field']}")
            print(f"  Expected: {failure['expected']}")
            print(f"  Actual:   {failure['actual']}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
