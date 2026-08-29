from __future__ import annotations

__all__ = ["extract_text_from_image"]

import base64
import io
import logging
import os
from typing import Any

import cv2
import easyocr
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

reader = easyocr.Reader(["en", "hi"])


def _detect_language(text: str) -> str:
    """
    Conservative language classification for the agreed OCR schema.

    EasyOCR returns bbox/text/confidence, but not a per-token language field.
    For the current English/Hindi scope:
      - Devanagari text -> hi
      - otherwise -> en
    """
    if not text:
        return "en"

    for char in text:
        if "\u0900" <= char <= "\u097F":
            return "hi"

    return "en"


def _to_ocr_tokens(results: list[Any]) -> list[dict[str, Any]]:
    """
    Convert EasyOCR detail=1 results to the application's token schema:

    {
        "text": string,
        "bbox": [[x,y], [x,y], [x,y], [x,y]],
        "confidence": float,
        "language": "en" | "hi"
    }
    """
    tokens: list[dict[str, Any]] = []

    for result in results:
        if not isinstance(result, (list, tuple)) or len(result) != 3:
            continue

        bbox, text, confidence = result

        if not isinstance(text, str):
            text = str(text)

        try:
            bbox_list = np.asarray(bbox).tolist()
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            continue

        tokens.append(
            {
                "text": text,
                "bbox": bbox_list,
                "confidence": confidence_value,
                "language": _detect_language(text),
            }
        )

    return tokens


async def extract_text_from_image(
    image_bytes: bytes | str,
) -> list[dict[str, Any]]:
    try:
        if isinstance(image_bytes, str):
            # router.py passes an uploaded image filesystem path.
            if os.path.isfile(image_bytes):
                with open(image_bytes, "rb") as f:
                    image_bytes = f.read()
            else:
                # Preserve existing base64/data-URL compatibility.
                if "," in image_bytes:
                    image_bytes = image_bytes.split(",", 1)[1]
                image_bytes = base64.b64decode(image_bytes)

        image = Image.open(io.BytesIO(image_bytes))
        image_array = np.array(image)

        if image_array.ndim == 3 and image_array.shape[-1] == 4:
            image_array = cv2.cvtColor(
                image_array,
                cv2.COLOR_RGBA2RGB,
            )

        # detail=1 preserves bbox + text + confidence.
        results = reader.readtext(image_array, detail=1)

        return _to_ocr_tokens(results)

    except Exception as e:
        logger.exception(
            "Failed to extract text from image bytes/path: %s",
            e,
        )
        return []
