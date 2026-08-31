from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.schema import ScanResult, ViolationRecord

from PIL import Image
import pillow_heif

pillow_heif.register_heif_opener()

from app.services.ocr import extract_text_from_image
from app.services.field_mapping_fallback import map_fields_with_fallback
from app.services.field_mapping_adapter import build_field_mapping_output
from app.services.rule_engine import check_compliance
from app.services.font_size_adapter import try_check_font_size


logger = logging.getLogger(__name__)

router = APIRouter()

UPLOAD_DIR = "uploads"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/health")
async def health_check() -> dict:
    return {
        "status": "healthy",
        "timestamp": utcnow().isoformat(),
    }


@router.post(
    "/scans/init",
    status_code=status.HTTP_201_CREATED,
)
async def init_scan(
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Main automated pipeline.

    Font-size checking is intentionally NOT performed here because it
    requires a manually supplied tap point. It is handled separately by
    /scans/{scan_id}/font-check.

    OCR failures are handled gracefully: the scan continues with an empty
    OCR result so the normal Field Mapping and compliance logic can report
    the supported fields as missing instead of crashing the request.
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    file_extension = (
        os.path.splitext(image.filename or "")[1]
        or ".jpg"
    )

    unique_filename = (
        f"{uuid.uuid4()}{file_extension}"
    )

    image_path = os.path.join(
        UPLOAD_DIR,
        unique_filename,
    )

    contents = await image.read()

    with open(
        image_path,
        "wb",
    ) as f:
        f.write(contents)

    await image.close()

    if image_path.lower().endswith(
        (".heic", ".heif")
    ):
        img = Image.open(image_path)

        new_image_path = (
            os.path.splitext(image_path)[0]
            + ".jpg"
        )

        img.convert("RGB").save(
            new_image_path,
            "JPEG",
        )

        os.remove(image_path)

        image_path = new_image_path

    # ------------------------------------------------------------------
    # OCR
    # ------------------------------------------------------------------
    #
    # OCR is an upstream dependency. A failure here should not crash the
    # entire scan request. The rest of the pipeline can safely operate on
    # an empty OCR result and report the corresponding missing fields.
    #
    try:
        ocr_tokens = await extract_text_from_image(
            image_path
        )

    except Exception:
        logger.exception(
            "OCR failed for image %s",
            image_path,
        )
        ocr_tokens = []

    if not ocr_tokens:
        ocr_tokens = []

    # ------------------------------------------------------------------
    # Field Mapping
    # ------------------------------------------------------------------

    mapping_result_dict = await map_fields_with_fallback(
        ocr_tokens,
        image_path=image_path,
)

    mapping_output = build_field_mapping_output(
        mapping_result_dict
    )

    # ------------------------------------------------------------------
    # Compliance
    # ------------------------------------------------------------------

    compliance_result = check_compliance(
        mapping_output
    )

    # ------------------------------------------------------------------
    # Persist scan result
    # ------------------------------------------------------------------

    scan_result = ScanResult(
        id=uuid.uuid4(),
        product_id=None,
        image_path=image_path,
        status="completed",
        is_compliant=compliance_result.is_compliant,
        compliance_score=compliance_result.score,
        raw_ocr=ocr_tokens,
        extracted_fields=mapping_result_dict,
        created_at=utcnow(),
    )

    db.add(scan_result)

    for violation in compliance_result.violations:
        db.add(
            ViolationRecord(
                scan_id=scan_result.id,
                field_name=violation.field_name,
                issue=violation.issue,
                severity=violation.severity,
                bbox=(
                    violation.bbox.model_dump()
                    if violation.bbox is not None
                    else None
                ),
                legal_reference=violation.legal_reference,
            )
        )

    await db.commit()

    await db.refresh(
        scan_result
    )

    return {
        "scan_id": str(scan_result.id),
        "status": scan_result.status,
        "image_path": scan_result.image_path,
        "is_compliant": compliance_result.is_compliant,
        "score": compliance_result.score,
        "violations": [
            {
                "field_name": violation.field_name,
                "issue": violation.issue,
                "severity": violation.severity,
                "bbox": (
                    [
                        violation.bbox.xmin,
                        violation.bbox.ymin,
                        violation.bbox.xmax,
                        violation.bbox.ymax,
                    ]
                    if violation.bbox
                    else None
                ),
                "legal_reference": violation.legal_reference,
            }
            for violation in compliance_result.violations
        ],
    }


@router.get("/scans/{scan_id}")
async def get_scan(
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(ScanResult)
        .options(
            selectinload(
                ScanResult.violations
            )
        )
        .where(
            ScanResult.id == scan_id
        )
    )

    scan_result = (
        result.scalar_one_or_none()
    )

    if scan_result is None:
        raise HTTPException(
            status_code=404,
            detail="Scan not found",
        )

    return {
        "id": str(scan_result.id),
        "product_id": (
            str(scan_result.product_id)
            if scan_result.product_id
            else None
        ),
        "image_path": scan_result.image_path,
        "status": scan_result.status,
        "is_compliant": scan_result.is_compliant,
        "compliance_score": scan_result.compliance_score,
        "raw_ocr": scan_result.raw_ocr,
        "extracted_fields": scan_result.extracted_fields,
        "created_at": (
            scan_result.created_at.isoformat()
        ),
        "violations": [
            {
                "id": str(violation.id),
                "field_name": violation.field_name,
                "issue": violation.issue,
                "severity": violation.severity,
                "bbox": violation.bbox,
                "legal_reference": violation.legal_reference,
                "created_at": (
                    violation.created_at.isoformat()
                ),
            }
            for violation in scan_result.violations
        ],
    }


class FontCheckRequest(BaseModel):
    tap_x: int
    tap_y: int
    coin_key: str = "5_rupee"
    net_quantity_g_or_ml: Optional[float] = None


@router.post(
    "/scans/{scan_id}/font-check"
)
async def font_check(
    scan_id: uuid.UUID,
    body: FontCheckRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    OPTIONAL, separate from the main automated pipeline.

    Requires a manually supplied tap point identifying the reference
    coin's approximate location. This endpoint never changes the stored
    main scan compliance result.
    """
    result = await db.execute(
        select(ScanResult).where(
            ScanResult.id == scan_id
        )
    )

    scan_result = (
        result.scalar_one_or_none()
    )

    if scan_result is None:
        raise HTTPException(
            status_code=404,
            detail="Scan not found",
        )

    font_result = try_check_font_size(
        image_path=scan_result.image_path,
        ocr_tokens=scan_result.raw_ocr or [],
        tap_point=(
            body.tap_x,
            body.tap_y,
        ),
        coin_key=body.coin_key,
        net_quantity_g_or_ml=(
            body.net_quantity_g_or_ml
        ),
    )

    if font_result is None:
        return {
            "scan_id": str(scan_id),
            "available": False,
            "message": (
                "Font-size check could not be completed -- "
                "no coin detected near the given tap point, "
                "or the image could not be re-read. This does "
                "not affect the scan's main compliance result."
            ),
        }

    return {
        "scan_id": str(scan_id),
        "available": True,
        "result": font_result,
    }