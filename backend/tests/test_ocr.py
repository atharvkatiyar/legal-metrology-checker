import sys
import asyncio
from pathlib import Path

# Add the backend directory to Python's import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ocr import extract_text_from_image

PASS = 0
FAIL = 0
FAILURES = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {detail}")
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail and not condition else ""))


def run_async(coro):
    return asyncio.run(coro)


REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_IMAGE_PATH = str(REPO_ROOT / "labeled_batch" / "images" / "IMG_003 (Himalaya Shampoo).jpg")


result = run_async(extract_text_from_image(SAMPLE_IMAGE_PATH))

check("File path input: returns a list", isinstance(result, list), f"got {type(result)}")
check("File path input: at least one text block detected", len(result) > 0, f"got {len(result)} blocks")

if result:
    first_block = result[0]
    check("Block shape: has 'text' key", "text" in first_block, f"got keys {list(first_block.keys())}")
    check("Block shape: has 'bbox' key", "bbox" in first_block, f"got keys {list(first_block.keys())}")
    check("Block shape: has 'confidence' key", "confidence" in first_block, f"got keys {list(first_block.keys())}")
    check("Block shape: has 'language' key", "language" in first_block, f"got keys {list(first_block.keys())}")

    check("Block shape: 'text' is a string", isinstance(first_block["text"], str), f"got {type(first_block['text'])}")
    check("Block shape: 'confidence' is a float between 0 and 1",
          isinstance(first_block["confidence"], float) and 0.0 <= first_block["confidence"] <= 1.0,
          f"got {first_block['confidence']}")
    check("Block shape: 'language' is always 'en' (Hindi dropped from scope)",
          first_block["language"] == "en", f"got {first_block['language']}")

    bbox = first_block["bbox"]
    check("Block shape: 'bbox' has exactly 4 points", len(bbox) == 4, f"got {len(bbox)} points")
    check("Block shape: each bbox point is an [x, y] pair of ints",
          all(len(pt) == 2 and isinstance(pt[0], int) and isinstance(pt[1], int) for pt in bbox),
          f"got {bbox}")

check("All blocks: every block has all 4 required keys",
      all({"text", "bbox", "confidence", "language"} <= set(b.keys()) for b in result),
      "some block is missing a required key")
check("All blocks: every block's language is 'en'",
      all(b["language"] == "en" for b in result),
      "some block has a language other than 'en'")


with open(SAMPLE_IMAGE_PATH, "rb") as f:
    image_bytes = f.read()

result_bytes = run_async(extract_text_from_image(image_bytes))
check("Raw bytes input: returns a list", isinstance(result_bytes, list), f"got {type(result_bytes)}")
check("Raw bytes input: at least one text block detected", len(result_bytes) > 0, f"got {len(result_bytes)} blocks")


import base64
b64_string = base64.b64encode(image_bytes).decode("utf-8")

result_b64 = run_async(extract_text_from_image(b64_string))
check("Base64 string input: returns a list", isinstance(result_b64, list), f"got {type(result_b64)}")
check("Base64 string input: at least one text block detected", len(result_b64) > 0, f"got {len(result_b64)} blocks")


result_bad_path = run_async(extract_text_from_image("this/path/does/not/exist.jpg"))
check("Nonexistent file path: returns empty list, does not crash", result_bad_path == [], f"got {result_bad_path}")

result_bad_bytes = run_async(extract_text_from_image(b"not a real image"))
check("Garbage bytes input: returns empty list, does not crash", result_bad_bytes == [], f"got {result_bad_bytes}")

result_empty_str = run_async(extract_text_from_image(""))
check("Empty string input: returns empty list, does not crash", result_empty_str == [], f"got {result_empty_str}")


import json as _json
try:
    _json.dumps(result)
    json_ok = True
except (TypeError, ValueError) as e:
    json_ok = False
    _json_error = str(e)
check("Structured output is JSON-serializable", json_ok, "" if json_ok else _json_error)


print("\n" + "=" * 60)
print(f"TOTAL: {PASS + FAIL}   PASSED: {PASS}   FAILED: {FAIL}")
if FAILURES:
    print("\nFailed tests:")
    for f in FAILURES:
        print(f"  - {f}")
print("=" * 60)

if __name__ == "__main__":
    sys.exit(0 if FAIL == 0 else 1)