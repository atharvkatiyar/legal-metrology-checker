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

# FIX (Issue 4): real ₹ character, not the mis-decoded "â‚¹" mojibake.
_TRIGGER_PATTERN = re.compile(
    r'(?:mrp|₹|rs\.?|mfd|exp|\d{2}[/.-]\d{2})',
    re.IGNORECASE,
)

_ROTATIONS: tuple[int | None, ...] = (
    None,
    cv2.ROTATE_90_CLOCKWISE,
    cv2.ROTATE_180,
    cv2.ROTATE_90_COUNTERCLOCKWISE,
)


def _get_reader() -> easyocr.Reader:
    """
    Lazily initialize EasyOCR once.
    English-only per current scope (Hindi support removed).
    """
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
    """
    Reduce excessively large images before OCR to limit memory usage.
    Aspect ratio is preserved.
    """
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


def _rotate(image_array: np.ndarray, rotation_code: int | None) -> np.ndarray:
    if rotation_code is None:
        return image_array
    return cv2.rotate(image_array, rotation_code)


def _score_results(results: list[Any]) -> float:
    """
    Rewards actual word lengths and statutory keywords to prevent
    "barcode shattering" (where sideways barcodes generate 50+ tiny
    garbage characters that artificially inflate the score).

    Never raises: any malformed entry is skipped rather than allowed
    to propagate, since a single bad detection must not disqualify an
    otherwise-good rotation candidate.
    """
    if not results:
        return 0.0

    score = 0.0
    for r in results:
        try:
            if not isinstance(r, (list, tuple)) or len(r) != 3:
                continue

            text = str(r[1]).strip()
            conf = float(r[2])

            # Penalize 1 or 2 character noise (likely barcode lines or color dots)
            if len(text) <= 2:
                score += conf * 0.1
            else:
                # Reward longer coherent strings
                score += conf * len(text)

            # MASSIVE BONUS: If this rotation reveals a compliance keyword,
            # it is very likely the correct rotation.
            if re.search(
                r'(?:mrp|₹|rs\.?|net|qty|mfd|exp|batch|date|manufactur)',
                text,
                re.IGNORECASE,
            ):
                score += 100.0
        except (TypeError, ValueError, IndexError):
            # FIX (Issue 5): don't let one malformed detection blow up
            # scoring for the whole rotation candidate.
            continue

    return score


def _detect_best_rotation(
    image_array: np.ndarray,
    ocr_reader: easyocr.Reader,
) -> np.ndarray:
    """
    Probes all 4 orientations with a single lightweight OCR pass each
    and returns the image rotated to whichever orientation produced
    the strongest, most numerous text detections.

    EasyOCR scans left-to-right and does not auto-rotate on its own,
    so a label photo taken sideways or upside-down otherwise produces
    near-total gibberish that the downstream regex/field-mapping
    engine can never recover from.

    FIX (Issue 5): every step of this probe is isolated with its own
    try/except so a single failed rotation (readtext exception, or a
    scoring exception) degrades gracefully to "skip this candidate"
    rather than propagating out to the caller's outermost handler and
    discarding all OCR tokens for the whole image.
    """
    best_array = image_array
    best_score = -1.0

    for rotation_code in _ROTATIONS:
        try:
            candidate = _rotate(image_array, rotation_code)
        except Exception:
            logger.exception(
                "Rotation step failed for rotation_code=%s",
                rotation_code,
            )
            continue

        try:
            results = ocr_reader.readtext(candidate, detail=1)
        except Exception:
            logger.exception(
                "Rotation-probe OCR pass failed for rotation_code=%s",
                rotation_code,
            )
            continue

        try:
            score = _score_results(results)
        except Exception:
            logger.exception(
                "Rotation-probe scoring failed for rotation_code=%s",
                rotation_code,
            )
            continue

        if score > best_score:
            best_score = score
            best_array = candidate

    return best_array


def _to_ocr_tokens(results: list[Any]) -> list[dict[str, Any]]:
    """
    Convert EasyOCR detail=1 results to the application's token schema:
    {
        "text": string,
        "bbox": [[x,y], [x,y], [x,y], [x,y]],
        "confidence": float,
        "language": "en"
    }

    Language is fixed to "en" -- Hindi detection/support has been
    removed from this module per current scope.
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
                "language": "en",
            }
        )
    return tokens


def _preprocess_dot_matrix(image_array: np.ndarray) -> np.ndarray:
    """
    Recovers faint / low-contrast / dot-matrix stamped text (e.g. MRP or
    batch codes stamped on can bottoms or crimps) via CLAHE contrast
    normalization, adaptive thresholding, and morphological closing to
    fuse isolated dot-matrix dots into cohesive alphanumeric strokes.
    """
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
    """
    Triggers the dot-matrix preprocessing pass when Pass 1 found no
    currency/price/date-shaped signal at all, or when average token
    confidence is low. An empty token list always triggers Pass 2.
    """
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
    """
    Merges Pass 1 and Pass 2 tokens. When two tokens' bounding boxes
    overlap with IoU > 0.5, the higher-confidence token is retained;
    non-overlapping tokens from both passes are kept.
    """
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

        # Rotation pre-pass: EasyOCR reads left-to-right and does not
        # auto-correct sideways/upside-down photos on its own. Probe
        # all 4 orientations cheaply and lock in the best one before
        # running the full (potentially two-pass) pipeline below.
        image_array = _detect_best_rotation(image_array, ocr_reader)

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