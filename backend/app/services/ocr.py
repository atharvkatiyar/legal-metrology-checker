from __future__ import annotations

__all__ = ["extract_text_from_image"]

import base64
import io
import logging
import os
import re
from typing import Any

import cv2
import easyocr
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_reader: easyocr.Reader | None = None

_TRIGGER_PATTERN = re.compile(
    r'(?:mrp|â‚¹|rs\.?|mfd|exp|\d{2}[/.-]\d{2})',
    re.IGNORECASE,
)


def _get_reader() -> easyocr.Reader:
    
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(
            ["en"],
            gpu=False,
            verbose=False,
        )
    return _reader


def _resize_for_ocr(
    image_array: np.ndarray,
    max_dimension: int = 2000,
) -> np.ndarray:
   
    height, width = image_array.shape[:2]
    largest_dimension = max(height, width)
    if largest_dimension <= max_dimension:
        return image_array
    scale = max_dimension / largest_dimension
    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))
    return cv2.resize(
        image_array,
        (new_width, new_height),
        interpolation=cv2.INTER_AREA,
    )


def _to_ocr_tokens(results: list[Any]) -> list[dict[str, Any]]:
    
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
                "language": "en",
            }
        )
    return tokens


def _preprocess_dot_matrix(image_array: np.ndarray) -> np.ndarray:
   
    if image_array.ndim == 3:
        if image_array.shape[-1] == 4:
            gray = cv2.cvtColor(image_array, cv2.COLOR_RGBA2GRAY)
        else:
            gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = image_array.copy()

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    contrast_enhanced = clahe.apply(gray)

    blurred = cv2.GaussianBlur(contrast_enhanced, (3, 3), 0)

    thresholded = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        11,
        2,
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    closed = cv2.morphologyEx(thresholded, cv2.MORPH_CLOSE, kernel)

    return closed


def _should_trigger_second_pass(tokens: list[dict[str, Any]]) -> bool:
    
    if not tokens:
        return True

    combined_text = " ".join(t.get("text", "") for t in tokens)
    has_signal = bool(_TRIGGER_PATTERN.search(combined_text))

    confidences = [
        t.get("confidence")
        for t in tokens
        if isinstance(t.get("confidence"), (int, float))
    ]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    return (not has_signal) or (avg_confidence < 0.45)


def _polygon_to_rect(bbox: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return min(xs), min(ys), max(xs), max(ys)


def _iou(bbox_a: list[list[float]], bbox_b: list[list[float]]) -> float:
    ax1, ay1, ax2, ay2 = _polygon_to_rect(bbox_a)
    bx1, by1, bx2, by2 = _polygon_to_rect(bbox_b)

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union_area = area_a + area_b - inter_area

    if union_area <= 0:
        return 0.0
    return inter_area / union_area


def _merge_and_deduplicate(
    primary_tokens: list[dict[str, Any]],
    secondary_tokens: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    
    merged: list[dict[str, Any]] = list(primary_tokens)

    for candidate in secondary_tokens:
        candidate_bbox = candidate.get("bbox")
        if not candidate_bbox:
            merged.append(candidate)
            continue

        overlap_index = None
        for idx, existing in enumerate(merged):
            existing_bbox = existing.get("bbox")
            if not existing_bbox:
                continue
            if _iou(candidate_bbox, existing_bbox) > 0.5:
                overlap_index = idx
                break

        if overlap_index is None:
            merged.append(candidate)
        else:
            existing_confidence = merged[overlap_index].get("confidence") or 0.0
            candidate_confidence = candidate.get("confidence") or 0.0
            if candidate_confidence > existing_confidence:
                merged[overlap_index] = candidate

    return merged


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

        image_array = _resize_for_ocr(image_array)
        ocr_reader = _get_reader()

        # Pass 1: standard extraction.
        pass_one_results = ocr_reader.readtext(image_array, detail=1)
        pass_one_tokens = _to_ocr_tokens(pass_one_results)

        if not _should_trigger_second_pass(pass_one_tokens):
            return pass_one_tokens

        # Pass 2: dot-matrix/stamped-text recovery pass.
        preprocessed_array = _preprocess_dot_matrix(image_array)
        pass_two_results = ocr_reader.readtext(preprocessed_array, detail=1)
        pass_two_tokens = _to_ocr_tokens(pass_two_results)

        return _merge_and_deduplicate(pass_one_tokens, pass_two_tokens)

    except Exception as e:
        logger.exception(
            "Failed to extract text from image bytes/path: %s",
            e,
        )
        return []