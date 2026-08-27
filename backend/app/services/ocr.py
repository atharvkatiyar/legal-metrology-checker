from __future__ import annotations

__all__ = ["extract_text_from_image"]

import base64
import io
import logging

import cv2
import easyocr
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

reader = easyocr.Reader(['en', 'hi'])


async def extract_text_from_image(image_bytes: bytes | str) -> str:
    try:
        if isinstance(image_bytes, str):
            if "," in image_bytes:
                image_bytes = image_bytes.split(",")[1]
            image_bytes = base64.b64decode(image_bytes)

        image = Image.open(io.BytesIO(image_bytes))
        image_array = np.array(image)

        if image_array.ndim == 3 and image_array.shape[-1] == 4:
            image_array = cv2.cvtColor(image_array, cv2.COLOR_RGBA2RGB)

        results = reader.readtext(image_array, detail=0)

        return " ".join(results)

    except Exception as e:
        logger.exception("Failed to extract text from image bytes: %s", e)
        return ""