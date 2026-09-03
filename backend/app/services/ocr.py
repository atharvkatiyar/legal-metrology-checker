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

reader = easyocr.Reader(['en'])

LOW_RES_THRESHOLD = 800  # if the shorter side is under this many pixels, upscale


def _deskew(image):
   
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 20:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.5:
        return image

    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image, M, (w, h),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _normalize_lighting(image):
   
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_eq = clahe.apply(l)
    merged = cv2.merge((l_eq, a, b))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def _upscale_and_denoise(image):
   
    h, w = image.shape[:2]
    shorter_side = min(h, w)
    is_low_res = shorter_side < LOW_RES_THRESHOLD

    if is_low_res:
        scale = LOW_RES_THRESHOLD / shorter_side
        new_w, new_h = int(w * scale), int(h * scale)
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        image = cv2.fastNlMeansDenoisingColored(
            image, None, h=7, hColor=7,
            templateWindowSize=7, searchWindowSize=21
        )
    return image


def _preprocess(image):
    
    image = _upscale_and_denoise(image)
    image = _deskew(image)
    image = _normalize_lighting(image)
    return image


async def extract_text_from_image(image_bytes: bytes | str) -> list[dict]:
   
    try:
        if isinstance(image_bytes, str):
            if "," in image_bytes:
                image_bytes = image_bytes.split(",")[1]
            image_bytes = base64.b64decode(image_bytes)

        image = Image.open(io.BytesIO(image_bytes))
        image_array = np.array(image)
        if image_array.ndim == 3 and image_array.shape[-1] == 4:
            image_array = cv2.cvtColor(image_array, cv2.COLOR_RGBA2RGB)

        processed = _preprocess(image_array)
        raw_results = reader.readtext(processed)

        structured = []
        for bbox, text, confidence in raw_results:
            clean_bbox = [[int(x), int(y)] for x, y in bbox]
            structured.append({
                "text": text,
                "bbox": clean_bbox,
                "confidence": round(float(confidence), 4),
                "language": "en",
            })

        return structured

    except Exception as e:
        logger.exception("Failed to extract text from image bytes: %s", e)
        return []