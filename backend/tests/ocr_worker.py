from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Make backend/ importable when this file is executed directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ocr import extract_text_from_image


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python tests/ocr_worker.py <image_path>"
        )

    image_path = sys.argv[1]

    tokens = await extract_text_from_image(image_path)

    # JSON goes to stdout.
    # Warnings/logging are separate and will be captured by the parent.
    print(json.dumps(tokens, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())