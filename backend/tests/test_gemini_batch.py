from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent

sys.path.insert(0, str(BACKEND_DIR))

from app.services.gemini_service import extract_fields_from_image
LABELS_PATH = ROOT / "labeled_batch" / "labels.json"
IMAGES_DIR = ROOT / "labeled_batch" / "images"

OUTPUT_PATH = (
    BACKEND_DIR
    / "tests"
    / "results"
    / "gemini_aug30_v2.json"
)

FIELDS = (
    "MRP",
    "NET_QUANTITY",
    "MANUFACTURER_ADDRESS",
    "MANUFACTURING_DATE",
    "CONSUMER_CARE",
)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).lower()

    text = (
        text
        .replace("–", "-")
        .replace("—", "-")
        .replace("’", "'")
    )

    return " ".join(text.split())


def extract_number(value: Any) -> float | None:
    if value is None:
        return None

    text = str(value)

    # Handle prices such as:
    # ₹ 3 599.00
    # Rs. 3599.00
    text = re.sub(
        r"(?<=\d)\s+(?=\d)",
        "",
        text,
    )

    matches = re.findall(
        r"\d[\d,]*(?:\.\d+)?",
        text,
    )

    if len(matches) != 1:
        return None

    try:
        return float(
            matches[0].replace(",", "")
        )
    except ValueError:
        return None


def normalize_quantity(
    value: Any,
) -> dict[str, Any] | None:
    if value is None:
        return None

    text = normalize_text(value)

    replacements = {
        "millilitres": "ml",
        "milliliters": "ml",
        "millilitre": "ml",
        "milliliter": "ml",
        "kilograms": "kg",
        "kilogram": "kg",
        "grams": "g",
        "gram": "g",
        "litres": "l",
        "liters": "l",
        "litre": "l",
        "liter": "l",
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


def extract_emails(value: Any) -> set[str]:
    if value is None:
        return set()

    return {
        email.lower()
        for email in re.findall(
            r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
            str(value),
            flags=re.IGNORECASE,
        )
    }


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)

    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]

    return digits


def extract_phones(value: Any) -> set[str]:
    if value is None:
        return set()

    text = str(value)

    patterns = (
        r"\+?91[\s-]?\d{10}",
        r"\b\d{10}\b",
        r"\b\d{3,5}[-\s]\d{6,8}\b",
        r"\b\d{2,5}(?:[-\s]\d{2,5}){1,3}\b",
    )

    phones: set[str] = set()

    for pattern in patterns:
        for match in re.findall(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            normalized = normalize_phone(match)

            if normalized:
                phones.add(normalized)

    return phones


def normalize_date(value: Any) -> str | None:
    if value is None:
        return None

    text = normalize_text(value)

    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }

    match = re.fullmatch(
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        text,
    )

    if match:
        return (
            f"{int(match.group(1)):04d}-"
            f"{int(match.group(2)):02d}-"
            f"{int(match.group(3)):02d}"
        )

    match = re.fullmatch(
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})",
        text,
    )

    if match:
        return (
            f"{int(match.group(3)):04d}-"
            f"{int(match.group(2)):02d}-"
            f"{int(match.group(1)):02d}"
        )

    match = re.fullmatch(
        r"(\d{1,2})/(\d{4})",
        text,
    )

    if match:
        return (
            f"{int(match.group(2)):04d}-"
            f"{int(match.group(1)):02d}"
        )

    match = re.fullmatch(
        r"([a-z]{3,9})[.\s/-]+(\d{2,4})",
        text,
        flags=re.IGNORECASE,
    )

    if match:
        month = months.get(
            match.group(1)[:3].lower()
        )

        if month is None:
            return None

        year = int(match.group(2))

        if year < 100:
            year += 2000

        return (
            f"{year:04d}-{month:02d}"
        )

    match = re.fullmatch(
        r"(\d{1,2})/([a-z]{3,9})/(\d{2,4})",
        text,
        flags=re.IGNORECASE,
    )

    if match:
        month = months.get(
            match.group(2)[:3].lower()
        )

        if month is None:
            return None

        year = int(match.group(3))

        if year < 100:
            year += 2000

        return (
            f"{year:04d}-"
            f"{month:02d}-"
            f"{int(match.group(1)):02d}"
        )

    return None


def consumer_care_matches(
    expected: Any,
    actual: Any,
) -> bool:
    if expected is None and actual is None:
        return True

    if expected is None or actual is None:
        return False

    expected_emails = extract_emails(expected)
    actual_emails = extract_emails(actual)

    expected_phones = extract_phones(expected)
    actual_phones = extract_phones(actual)

    if expected_emails and not expected_emails <= actual_emails:
        return False

    if expected_phones and not expected_phones <= actual_phones:
        return False

    if not expected_emails and not expected_phones:
        return (
            normalize_text(expected)
            in normalize_text(actual)
        )

    return True


def manufacturer_matches(
    expected: Any,
    actual: Any,
) -> bool:
    if expected is None and actual is None:
        return True

    if expected is None or actual is None:
        return False

    return (
        normalize_text(expected)
        in normalize_text(actual)
    )


def field_matches(
    field: str,
    expected: Any,
    actual: Any,
) -> bool:
    if expected is None and actual is None:
        return True

    if expected is None or actual is None:
        return False

    if field == "MRP":
        return (
            extract_number(expected)
            == extract_number(actual)
        )

    if field == "NET_QUANTITY":
        return (
            normalize_quantity(expected)
            == normalize_quantity(actual)
        )

    if field == "MANUFACTURING_DATE":
        return (
            normalize_date(expected)
            == normalize_date(actual)
        )

    if field == "CONSUMER_CARE":
        return consumer_care_matches(
            expected,
            actual,
        )

    if field == "MANUFACTURER_ADDRESS":
        return manufacturer_matches(
            expected,
            actual,
        )

    return (
        normalize_text(expected)
        == normalize_text(actual)
    )


def main() -> None:
    if not LABELS_PATH.exists():
        raise FileNotFoundError(
            LABELS_PATH
        )

    with open(
        LABELS_PATH,
        "r",
        encoding="utf-8",
    ) as f:
        labels = json.load(f)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    totals = {
        field: 0
        for field in FIELDS
    }

    passes = {
        field: 0
        for field in FIELDS
    }

    print("=" * 72)
    print("AUG 30 — GEMINI LIVE FIELD EXTRACTION EVALUATION")
    print("=" * 72)
    print(f"Dataset size: {len(labels)} images")
    print("Model: gemini-3.6-flash")
    print("Execution: sequential")
    print()

    for index, record in enumerate(
        labels,
        start=1,
    ):
        image_name = record["image"]
        image_path = IMAGES_DIR / image_name

        print(
            f"[{index}/{len(labels)}] "
            f"Processing: {image_name}"
        )

        if not image_path.exists():
            error = f"Missing image: {image_path}"

            errors.append(
                {
                    "image": image_name,
                    "error": error,
                }
            )

            print(
                f"  [ERROR] {error}"
            )
            print()
            continue

        try:
            actual = extract_fields_from_image(
                image_path
            )

        except Exception as exc:
            error = (
                f"{type(exc).__name__}: {exc}"
            )

            errors.append(
                {
                    "image": image_name,
                    "error": error,
                }
            )

            print(
                f"  [ERROR] {error}"
            )
            print()
            continue

        field_results: dict[str, Any] = {}

        for field in FIELDS:
            expected = record.get(field)
            actual_value = actual.get(field)

            passed = field_matches(
                field,
                expected,
                actual_value,
            )

            totals[field] += 1

            if passed:
                passes[field] += 1

            status = (
                "PASS"
                if passed
                else "FAIL"
            )

            print(
                f"  [{status}] {field}"
            )

            field_results[field] = {
                "expected": expected,
                "actual": actual_value,
                "pass": passed,
            }

        results.append(
            {
                "image": image_name,
                "fields": field_results,
            }
        )

        print()

    total_passed = sum(
        passes.values()
    )

    total_cases = sum(
        totals.values()
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

    overall = (
        100 * total_passed / total_cases
        if total_cases
        else 0.0
    )

    report = {
        "model": "gemini-3.6-flash",
        "dataset_size": len(labels),
        "evaluated_images": len(results),
        "error_images": len(errors),
        "fields": field_report,
        "overall": {
            "passed": total_passed,
            "total": total_cases,
            "accuracy_percent": round(
                overall,
                2,
            ),
        },
        "errors": errors,
        "results": results,
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        OUTPUT_PATH,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            report,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print("=" * 72)
    print("GEMINI LIVE SEMANTIC ACCURACY")
    print("=" * 72)

    for field in FIELDS:
        item = field_report[field]

        print(
            f"{field:24s}"
            f"{item['passed']:2d}/{item['total']:2d} "
            f"({item['accuracy_percent']:6.2f}%)"
        )

    print("-" * 72)

    print(
        f"{'OVERALL':24s}"
        f"{total_passed:2d}/{total_cases:2d} "
        f"({overall:6.2f}%)"
    )

    print()
    print(
        f"Report written to: {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()