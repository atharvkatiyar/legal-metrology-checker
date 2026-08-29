from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


# Make backend/ importable when running:
# python tests/test_labeled_batch.py
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent

sys.path.insert(0, str(BACKEND_DIR))

LABELS_PATH = ROOT / "labeled_batch" / "labels.json"
IMAGES_DIR = ROOT / "labeled_batch" / "images"
WORKER_PATH = Path(__file__).with_name("ocr_worker.py")

FIELDS = (
    "MRP",
    "NET_QUANTITY",
    "MANUFACTURER_ADDRESS",
    "MANUFACTURING_DATE",
    "CONSUMER_CARE",
)


def run_ocr_isolated(
    image_path: Path,
    timeout: int = 120,
) -> list[dict[str, Any]]:
    """Run OCR in a short-lived subprocess for one image."""
    result = subprocess.run(
        [
            sys.executable,
            str(WORKER_PATH),
            str(image_path),
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"OCR worker failed for {image_path.name}\n"
            f"{result.stderr}"
        )

    try:
        tokens = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"OCR worker returned invalid JSON for {image_path.name}\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:1000]}"
        ) from exc

    if not isinstance(tokens, list):
        raise RuntimeError(
            f"OCR worker returned {type(tokens).__name__}; expected list"
        )

    return tokens


def normalize_expected(field: str, value: Any) -> Any:
    """Normalize labeled ground-truth values for comparison."""
    if value is None:
        return None

    if field == "MRP":
        text = str(value)

        text = re.sub(
            r"(?i)\b(?:MRP|MAXIMUM\s+RETAIL\s+PRICE|RS\.?|INR)\b",
            " ",
            text,
        )

        text = text.replace("₹", " ")
        text = text.replace("$", " ")
        text = text.replace("/-", " ")

        # "3 599.00" -> "3599.00"
        text = re.sub(
            r"(?<=\d)\s+(?=\d)",
            "",
            text,
        )

        match = re.search(
            r"\d[\d,]*(?:\.\d+)?",
            text,
        )

        if not match:
            return None

        try:
            return float(
                match.group(0).replace(",", "")
            )
        except ValueError:
            return None

    if field == "NET_QUANTITY":
        text = str(value).lower().strip()

        replacements = {
            "millilitres": "ml",
            "milliliters": "ml",
            "kilograms": "kg",
            "kilogram": "kg",
            "grams": "g",
            "gram": "g",
            "litres": "l",
            "liters": "l",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

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
        return " ".join(
            str(value).lower().split()
        )

    if field in {
        "MANUFACTURER_ADDRESS",
        "MANUFACTURING_DATE",
    }:
        return " ".join(
            str(value).lower().split()
        )

    return value


def normalize_actual(
    field: str,
    result: dict[str, Any],
) -> Any:
    """Normalize Field Mapping output for comparison."""
    value = result.get("value")

    if value is None:
        return None

    if field == "MRP":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    if field == "NET_QUANTITY":
        if not isinstance(value, dict):
            return None

        try:
            amount = float(value["amount"])
        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            return None

        unit = value.get("unit")

        if unit is None:
            return None

        return {
            "amount": amount,
            "unit": str(unit).lower(),
        }

    if field == "CONSUMER_CARE":
        if not isinstance(value, dict):
            return None

        parts = []

        phone = value.get("phone")
        email = value.get("email")

        if phone:
            parts.append(
                str(phone).lower()
            )

        if email:
            parts.append(
                str(email).lower()
            )

        return (
            " / ".join(parts)
            if parts
            else None
        )

    if field in {
        "MANUFACTURER_ADDRESS",
        "MANUFACTURING_DATE",
    }:
        return " ".join(
            str(value).lower().split()
        )

    return value


def evaluate_image(
    record: dict[str, Any],
) -> dict[str, Any]:
    """OCR and map one labeled image."""
    image_name = record["image"]
    image_path = IMAGES_DIR / image_name

    if not image_path.exists():
        raise FileNotFoundError(
            f"Missing labeled image: {image_path}"
        )

    # OCR runs only inside the isolated worker.
    tokens = run_ocr_isolated(image_path)

    from app.field_mapping import map_fields

    mapped = map_fields(tokens)

    fields = {}

    for field in FIELDS:
        expected = normalize_expected(
            field,
            record.get(field),
        )

        actual = normalize_actual(
            field,
            mapped[field],
        )

        fields[field] = {
            "expected": expected,
            "actual": actual,
            "pass": expected == actual,
        }

    return {
        "image": image_name,
        "ocr_token_count": len(tokens),
        "fields": fields,
    }


def main() -> None:
    if not LABELS_PATH.exists():
        raise FileNotFoundError(
            f"Missing labels file: {LABELS_PATH}"
        )

    if not IMAGES_DIR.exists():
        raise FileNotFoundError(
            f"Missing images directory: {IMAGES_DIR}"
        )

    if not WORKER_PATH.exists():
        raise FileNotFoundError(
            f"Missing OCR worker: {WORKER_PATH}"
        )

    with open(
        LABELS_PATH,
        "r",
        encoding="utf-8",
    ) as f:
        labels = json.load(f)

    if not isinstance(labels, list):
        raise ValueError(
            "labels.json must contain a list"
        )

    totals = {
        field: 0
        for field in FIELDS
    }

    passes = {
        field: 0
        for field in FIELDS
    }

    failures = []
    errors = []

    print("=" * 72)
    print(
        "AUG 29 — LABELED BATCH FIELD MAPPING EVALUATION"
    )
    print("=" * 72)
    print(
        f"Dataset size: {len(labels)} images"
    )
    print(
        "OCR mode: isolated subprocess per image"
    )
    print(
        "OCR execution: sequential"
    )
    print()

    for index, record in enumerate(
        labels,
        start=1,
    ):
        image_name = record.get(
            "image",
            "<missing image>",
        )

        print(
            f"[{index}/{len(labels)}] "
            f"Processing: {image_name}"
        )

        try:
            result = evaluate_image(
                record
            )

        except Exception as exc:
            errors.append(
                {
                    "image": image_name,
                    "error": str(exc),
                }
            )

            print(
                f"  [ERROR] "
                f"{type(exc).__name__}: {exc}"
            )
            print()
            continue

        print(
            f"  OCR tokens: "
            f"{result['ocr_token_count']}"
        )

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

            print(
                f"  [{status}] {field}"
            )

        print()

    print("=" * 72)
    print("FIELD ACCURACY")
    print("=" * 72)

    for field in FIELDS:
        total = totals[field]
        passed = passes[field]

        accuracy = (
            100 * passed / total
            if total
            else 0.0
        )

        print(
            f"{field:24s}"
            f"{passed:2d}/{total:2d} "
            f"({accuracy:6.2f}%)"
        )

    total_passed = sum(
        passes.values()
    )

    total_cases = sum(
        totals.values()
    )

    overall = (
        100 * total_passed / total_cases
        if total_cases
        else 0.0
    )

    print("-" * 72)

    print(
        f"{'OVERALL':24s}"
        f"{total_passed:2d}/{total_cases:2d} "
        f"({overall:6.2f}%)"
    )

    print()
    print("=" * 72)
    print("FIELD-MAPPING FAILURE PATTERNS")
    print("=" * 72)

    if not failures:
        print("No field-mapping failures.")
    else:
        for failure in failures:
            print(
                f"[FAIL] {failure['image']}"
            )
            print(
                f"  Field:    {failure['field']}"
            )
            print(
                f"  Expected: {failure['expected']}"
            )
            print(
                f"  Actual:   {failure['actual']}"
            )
            print()

    print("=" * 72)
    print("OCR WORKER / INFRASTRUCTURE FAILURES")
    print("=" * 72)

    if not errors:
        print("No worker failures.")
    else:
        for error in errors:
            print(
                f"[ERROR] {error['image']}"
            )
            print(
                f"  Error: {error['error']}"
            )
            print()


if __name__ == "__main__":
    main()
