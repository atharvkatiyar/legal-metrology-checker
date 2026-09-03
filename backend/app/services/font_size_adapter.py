"""
Optional, isolated font-size wiring. Deliberately NOT called from the
main /scans/init flow — see Memory.md "Aug 29 — Font-Size module status
confirmed" for the full reasoning (manual tap_point requirement, token
shape mismatch, coin-presence assumption, uncaught ValueError risk).

This module exists so font-size CAN be demoed via a separate, explicit
endpoint without risking the primary automated compliance pipeline.
"""

from __future__ import annotations
from typing import Optional

import cv2

from app.services.font_size import check_font_size


def try_check_font_size(
    image_path: str,
    ocr_tokens: list[dict],
    tap_point: tuple[int, int],
    coin_key: str = "5_rupee",
    net_quantity_g_or_ml: Optional[float] = None,
) -> Optional[dict]:
    """
    Best-effort wrapper: returns None on ANY failure rather than raising,
    since this is an optional add-on check, never a blocker.
    """
    try:
        image_bgr = cv2.imread(image_path)
        if image_bgr is None:
            return None

        height, width = image_bgr.shape[:2]

        return check_font_size(
            ocr_tokens=ocr_tokens,
            image_dimensions=(width, height),
            image_bgr=image_bgr,
            tap_point=tap_point,
            coin_key=coin_key,
            net_quantity_g_or_ml=net_quantity_g_or_ml,
        )
    except Exception:
        return None