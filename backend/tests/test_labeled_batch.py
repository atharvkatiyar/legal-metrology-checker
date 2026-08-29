from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Paths / configuration
# ---------------------------------------------------------------------------

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

WORKER_PATH = Path(__file__).with_name("ocr_worker.py")

# Make backend/ importable when this test is launched as:
# python tests/test_labeled_batch.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Isolated OCR execution
# ---------------------------------------------------------------------------

def run_ocr_isolated(
    image_path: Path,
    timeout_seconds: int = 120,
) -> list[dict[str, Any]]:
    """
    Run OCR in a separate Python process.

    Each image gets its own process so EasyOCR/PyTorch memory is released
    when the worker exits. Images are processed sequentially.
    """
    result = subprocess.run(
        [
            sys.executable,
            str(WORKER_PATH),
            str(image_path),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        cwd=ROOT / "backend",
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"OCR worker failed for {image_path.name}\n"
            f"Return code: {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

    try:
        tokens = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"OCR worker returned invalid JSON for {image_path.name}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        ) from exc

    if not isinstance(tokens, list):
        raise RuntimeError(
            f"OCR worker returned {type(tokens).__name__} "
            f"for {image_path.name}; expected list"
        )

    return tokens


# ---------------------------------------------------------------------------
# Ground-truth normalization
# ---------------------------------------------------------------------------

def normalize_expected(
    field: str,
    value: Any,
) -> Any:
    """
    Normalize labeled ground-truth values for deterministic comparison.

    This function is used only by the evaluation script. It does not modify
    application Field Mapping behavior.
    """
    if value is None:
        return None

    # -----------------------------------------------------------------------
    # MRP
    # -----------------------------------------------------------------------

    if field == "MRP":
        text = str(value)

        # Remove common MRP/currency labels.
        text = re.sub(
            r"(?i)\b(?:MRP|MAXIMUM\s+RETAIL\s+PRICE|RS\.?|INR)\b",
            " ",
            text,
        )

        # Remove currency symbols.
        text = text.replace("₹", " ")
        text = text.replace("$", " ")

        # Remove '/-' suffix.
        text = text.replace("/-", " ")

        # Ground-truth data may contain OCR-style spaces inside a number:
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

        raw = match.group(0).replace(",", "")

        try:
            return float(raw)
        except ValueError:
            return None

    # -----------------------------------------------------------------------
    # NET QUANTITY
    # -----------------------------------------------------------------------

    if field == "NET_QUANTITY":
        text = str(value).lower().strip()

        replacements = (
            ("millilitres", "ml"),
            ("milliliters", "ml"),
            ("kilograms", "kg"),
            ("kilogram", "kg"),
            ("grams", "g"),
            ("gram", "g"),
            ("litres", "l"),
            ("liters", "l"),
        )

        for old, new in replacements:
            text = text.replace(old, new)

        # Find the first recognized amount + supported unit.
        #
        # Examples:
        #   "340 ml" -> 340 ml
        #   "200ml" -> 200 ml
        #   "1.5 l" -> 1.5 l
        #   "300ml (294.8g)" -> 300 ml
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

    # -----------------------------------------------------------------------
    # CONSUMER CARE
    # -----------------------------------------------------------------------

    if field == "CONSUMER_CARE":
        return " ".join(
            str(value).lower().split()
        )

    # -----------------------------------------------------------------------
    # MANUFACTURER / DATE
    # -----------------------------------------------------------------------

    if field in (
        "MANUFACTURER_ADDRESS",
        "MANUFACTURING_DATE",
    ):
        return " ".join(
            str(value).lower().split()
        )

    return value


# ---------------------------------------------------------------------------
# Actual Field Mapping result normalization
# ---------------------------------------------------------------------------

def normalize_actual(
    field: str,
    result: dict[str, Any],
) -> Any:
    """
    Normalize actual Field Mapping output for deterministic comparison.
    """
    value = result.get("value")

    if value is None:
        return None

    # -----------------------------------------------------------------------
    # MRP
    # -----------------------------------------------------------------------

    if field == "MRP":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # -----------------------------------------------------------------------
    # NET QUANTITY
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # CONSUMER CARE
    # -----------------------------------------------------------------------

    if field == "CONSUMER_CARE":
        if not isinstance(value, dict):
            return None

        phone = value.get("phone")
        email = value.get("email")

        parts: list[str] = []

        if phone:
            parts.append(
                str(phone).lower()
            )

        if email:
            parts.append(
                str(email).lower()
            )

        if not parts:
            return None

        return " / ".join(parts)

    # -----------------------------------------------------------------------
    # MANUFACTURER / DATE
    # -----------------------------------------------------------------------

    if field in (
        "MANUFACTURER_ADDRESS",
        "MANUFACTURING_DATE",
    ):
        return " ".join(
            str(value).lower().split()
        )

    return value


# ---------------------------------------------------------------------------
# Single-image evaluation
# ---------------------------------------------------------------------------

def evaluate_image(
    record: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate one labeled image.

    OCR runs in an isolated subprocess. Field Mapping runs in the parent
    process after the OCR worker returns its lightweight token list.
    """
    image_name = record["image"]

    image_path = IMAGES_DIR / image_name

    if not image_path.exists():
        raise FileNotFoundError(
            f"Missing labeled image: {image_path}"
        )

    # IMPORTANT:
    # Do NOT import EasyOCR here.
    # OCR happens entirely inside the isolated worker.
    ocr_tokens = run_ocr_isolated(
        image_path
    )

    from app.field_mapping import map_fields

    mapped = map_fields(
        ocr_tokens
    )

    field_results: dict[str, dict[str, Any]] = {}

    for field in FIELDS:
        expected = normalize_expected(
            field,
            record.get(field),
        )

        actual = normalize_actual(
            field,
            mapped[field],
        )

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


# ---------------------------------------------------------------------------
# Batch evaluation
# ---------------------------------------------------------------------------

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
            "labels.json must contain a list of records"
        )

    totals = {
        field: 0
        for field in FIELDS
    }

    passes = {
        field: 0
        for field in FIELDS
    }

    failures: list[dict[str, Any]] = []

    worker_failures: list[dict[str, Any]] = []

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

    # Process exactly one labeled image at a time.
    for index, record in enumerate(
        labels,
        start=1,
    ):
        image_name = record.get(
            "image",
            "<missing image>",
        )

        print(
            f"[{index}/{len(labels)}] Processing: "
            f"{image_name}"
        )

        try:
            result = evaluate_image(
                record
            )

        except Exception as exc:
            worker_failures.append(
                {
                    "image": image_name,
                    "error": str(exc),
                }
            )

            print(
                f"  [ERROR] {type(exc).__name__}: {exc}"
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

    # -----------------------------------------------------------------------
    # Accuracy summary
    # -----------------------------------------------------------------------

    print("=" * 72)
    print("FIELD ACCURACY")
    print("=" * 72)

    for field in FIELDS:
        total = totals[field]
        passed = passes[field]

        accuracy = (
            (passed / total) * 100
            if total
            else 0.0
        )

        print(
            f"{field:24s}"
            f"{passed:2d}/{total:2d} "
            f"({accuracy:6.2f}%)"
        )

    total_pass = sum(
        passes.values()
    )

    total_cases = sum(
        totals.values()
    )

    overall_accuracy = (
        (total_pass / total_cases) * 100
        if total_cases
        else 0.0
    )

    print("-" * 72)

    print(
        f"{'OVERALL':24s}"
        f"{total_pass:2d}/{total_cases:2d} "
        f"({overall_accuracy:6.2f}%)"
    )

    # -----------------------------------------------------------------------
    # Field-mapping failures
    # -----------------------------------------------------------------------

    print()
    print("=" * 72)
    print("FIELD-MAPPING FAILURE PATTERNS")
    print("=" * 72)

    if not failures:
        print(
            "No field-mapping failures."
        )

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

    # -----------------------------------------------------------------------
    # OCR worker / infrastructure failures
    # -----------------------------------------------------------------------

    print("=" * 72)
    print("OCR WORKER / INFRASTRUCTURE FAILURES")
    print("=" * 72)

    if not worker_failures:
        print(
            "No worker failures."
        )

    else:
        for failure in worker_failures:
            print(
                f"[ERROR] {failure['image']}"
            )
            print(
                f"  Error: {failure['error']}"
            )
            print()


if __name__ == "__main__":
    main()