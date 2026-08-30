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

LOW_RES_THRESHOLD = 800  # if the shorter side is under this many pixels, upscale
DEVANAGARI_RATIO_THRESHOLD = 0.3  # fraction of alphanumeric chars that must be Devanagari to call a line "hi"


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
    """
    Denoising is only applied when the image was actually low-res (and got
    upscaled). Testing showed denoising every image — including
    already-sharp, high-res photos — slightly softened fine text edges
    and reduced OCR confidence on clean images for no benefit.
    """
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


def _detect_language(text: str) -> str:
    """
    Requires a minimum proportion of Devanagari characters among the
    alphanumeric characters in the line, rather than flagging "hi" on a
    single stray misread digit — bilingual OCR sometimes reads Latin
    numerals as Devanagari numerals (e.g. "24" -> "२४"), which was
    previously enough to flip an entire English line's language tag.
    """
    devanagari_count = sum(1 for ch in text if '\u0900' <= ch <= '\u097F')
    alnum_count = sum(1 for ch in text if ch.isalnum())

    if alnum_count == 0:
        return "en"

    ratio = devanagari_count / alnum_count
    return "hi" if ratio >= DEVANAGARI_RATIO_THRESHOLD else "en"


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
                "language": _detect_language(text),
            })

        return structured

    except Exception as e:
        logger.exception("Failed to extract text from image bytes: %s", e)
        return []