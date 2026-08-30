from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def normalize_expected(
    field: str,
    value: Any,
) -> Any:
    """Normalize ground-truth values for deterministic comparison."""
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
    """Normalize Field Mapping output for deterministic comparison."""
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

        parts: list[str] = []

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

    tokens = run_ocr_isolated(image_path)

    from app.field_mapping import map_fields

    mapped = map_fields(tokens)

    fields: dict[str, dict[str, Any]] = {}

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


def build_report(
    dataset_size: int,
    results: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a machine-readable accuracy report."""
    totals = {
        field: 0
        for field in FIELDS
    }

    passes = {
        field: 0
        for field in FIELDS
    }

    failures: list[dict[str, Any]] = []

    for result in results:
        for field in FIELDS:
            field_result = result["fields"][field]

            totals[field] += 1

            if field_result["pass"]:
                passes[field] += 1
            else:
                failures.append(
                    {
                        "image": result["image"],
                        "field": field,
                        "expected": field_result["expected"],
                        "actual": field_result["actual"],
                    }
                )

    field_report: dict[str, Any] = {}

    for field in FIELDS:
        total = totals[field]
        passed = passes[field]

        field_report[field] = {
            "passed": passed,
            "total": total,
            "accuracy_percent": round(
                100 * passed / total,
                2,
            ) if total else 0.0,
        }

    total_passed = sum(
        passes.values()
    )

    total_cases = sum(
        totals.values()
    )

    overall_accuracy = (
        100 * total_passed / total_cases
        if total_cases
        else 0.0
    )

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_size": dataset_size,
        "evaluated_images": len(results),
        "error_images": len(errors),
        "fields": field_report,
        "overall": {
            "passed": total_passed,
            "total": total_cases,
            "accuracy_percent": round(
                overall_accuracy,
                2,
            ),
        },
        "errors": errors,
        "failures": failures,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate labeled OCR/Field Mapping performance "
            "and optionally save a JSON accuracy report."
        )
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path for the JSON accuracy report.",
    )

    return parser.parse_args()


def print_summary(
    report: dict[str, Any],
) -> None:
    print()
    print("=" * 72)
    print("FIELD ACCURACY")
    print("=" * 72)

    for field in FIELDS:
        item = report["fields"][field]

        print(
            f"{field:24s}"
            f"{item['passed']:2d}/{item['total']:2d} "
            f"({item['accuracy_percent']:6.2f}%)"
        )

    overall = report["overall"]

    print("-" * 72)

    print(
        f"{'OVERALL':24s}"
        f"{overall['passed']:2d}/{overall['total']:2d} "
        f"({overall['accuracy_percent']:6.2f}%)"
    )


def main() -> None:
    args = parse_args()

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

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    print("=" * 72)
    print("AUG 30 — LABELED BATCH FIELD MAPPING EVALUATION")
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
            result = evaluate_image(record)

        except Exception as exc:
            errors.append(
                {
                    "image": image_name,
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                }
            )

            print(
                f"  [ERROR] "
                f"{type(exc).__name__}: {exc}"
            )
            print()
            continue

        results.append(result)

        print(
            f"  OCR tokens: "
            f"{result['ocr_token_count']}"
        )

        for field in FIELDS:
            status = (
                "PASS"
                if result["fields"][field]["pass"]
                else "FAIL"
            )

            print(
                f"  [{status}] {field}"
            )

        print()

    report = build_report(
        dataset_size=len(labels),
        results=results,
        errors=errors,
    )

    print_summary(report)

    if args.output is not None:
        args.output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with open(
            args.output,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                report,
                f,
                indent=2,
                ensure_ascii=False,
            )

        print()
        print(
            f"Accuracy report written to: "
            f"{args.output}"
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