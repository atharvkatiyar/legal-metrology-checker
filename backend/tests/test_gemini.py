from __future__ import annotations

import sys
from pathlib import Path

# Make backend/ importable when running:
# python tests/test_gemini.py
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.gemini_service import extract_fields_from_image


IMAGE = (
    BACKEND_DIR.parent
    / "labeled_batch"
    / "images"
    / "IMG_003 (Himalaya Shampoo).jpg"
)


def main() -> None:
    result = extract_fields_from_image(IMAGE)

    print("GEMINI OK")
    print(result)


if __name__ == "__main__":
    main()