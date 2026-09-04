import sys
import asyncio
import json
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ocr import extract_text_from_image

FIELDS = ["MRP", "NET_QUANTITY", "MANUFACTURER_ADDRESS", "MANUFACTURING_DATE", "CONSUMER_CARE"]
MATCH_THRESHOLD = 0.6

REPO_ROOT = Path(__file__).resolve().parents[2]
BATCH_DIR = REPO_ROOT / "labeled_batch"
IMAGES_DIR = BATCH_DIR / "images"
LABELS_PATH = BATCH_DIR / "labels.json"


def tokenize(text):
    text = text.replace("â‚¹", "").replace("₹", "")
    raw_tokens = re.split(r'[^a-zA-Z0-9\u0900-\u097F]+', text.lower())
    word_tokens = [t for t in raw_tokens if t and (len(t) >= 2 or t.isdigit())]
    digit_tokens = re.findall(r'\d+', text)
    return list(set(word_tokens) | set(digit_tokens))


async def run_batch():
    with open(LABELS_PATH, "r", encoding="utf-8") as f:
        labels = json.load(f)

    field_stats = {field: {"total": 0, "found": 0, "match_ratios": []} for field in FIELDS}
    failure_log = []
    crash_log = []

    for entry in labels:
        image_name = entry["image"]
        image_path = IMAGES_DIR / image_name

        if not image_path.exists():
            print(f"WARNING: image not found, skipping: {image_name}")
            continue

        print(f"Processing: {image_name}")
        try:
            ocr_results = await extract_text_from_image(str(image_path))
        except Exception as e:
            print(f"  ERROR: {e}")
            crash_log.append((image_name, str(e)))
            for field in FIELDS:
                if entry.get(field):
                    field_stats[field]["total"] += 1
                    failure_log.append((image_name, field, entry[field], "OCR CRASHED", 0.0))
            continue

        ocr_tokens = set()
        for block in ocr_results:
            ocr_tokens.update(tokenize(block["text"]))

        for field in FIELDS:
            expected = entry.get(field)
            if not expected:
                continue

            field_stats[field]["total"] += 1
            expected_tokens = tokenize(expected)
            if not expected_tokens:
                continue

            matched = sum(1 for t in expected_tokens if t in ocr_tokens)
            match_ratio = matched / len(expected_tokens)
            field_stats[field]["match_ratios"].append(match_ratio)

            if match_ratio >= MATCH_THRESHOLD:
                field_stats[field]["found"] += 1
            else:
                failure_log.append((image_name, field, expected, "BELOW THRESHOLD", match_ratio))

    print("\n" + "=" * 70)
    print(f"DAY 9 ACCURACY REPORT — real services/ocr.py, {len(labels)} images")
    print("=" * 70)
    for field, stats in field_stats.items():
        total = stats["total"]
        found = stats["found"]
        if total == 0:
            continue
        pct = (found / total) * 100
        avg_ratio = (sum(stats["match_ratios"]) / len(stats["match_ratios"]) * 100) if stats["match_ratios"] else 0
        print(f"  {field}: {found}/{total} found ({pct:.1f}%) — avg word-match: {avg_ratio:.1f}%")

    if crash_log:
        print(f"\nCRASHES: {len(crash_log)}")
        for img, err in crash_log:
            print(f"  [{img}] {err}")

    print(f"\nFAILURES BELOW THRESHOLD: {len(failure_log)}")
    for img, field, expected, reason, ratio in failure_log:
        print(f"  [{img}] {field} — expected {expected!r} — {reason} ({ratio*100:.0f}%)")


if __name__ == "__main__":
    asyncio.run(run_batch())