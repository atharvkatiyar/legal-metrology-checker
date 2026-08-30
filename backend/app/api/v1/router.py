from __future__ import annotations
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
from app.field_mapping import map_fields
from app.services.field_mapping_adapter import build_field_mapping_output
from app.services.rule_engine import check_compliance
from app.services.font_size_adapter import try_check_font_size

router = APIRouter()
UPLOAD_DIR = "uploads"

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

@router.get("/health")
async def health_check() -> dict:
    return {"status": "healthy", "timestamp": utcnow().isoformat()}

@router.post("/scans/init", status_code=status.HTTP_201_CREATED)
async def init_scan(
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Main automated pipeline. Deliberately does NOT touch font-size --
    that check requires a manual tap_point that doesn't exist at upload
    time, so it's handled by the separate /scans/{scan_id}/font-check
    endpoint below, called after the fact if/when a user wants it.
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_extension = os.path.splitext(image.filename or "")[1] or ".jpg"
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    image_path = os.path.join(UPLOAD_DIR, unique_filename)

    contents = await image.read()
    with open(image_path, "wb") as f:
        f.write(contents)
    await image.close()

    if image_path.lower().endswith(('.heic', '.heif')):
        img = Image.open(image_path)
        new_image_path = os.path.splitext(image_path)[0] + ".jpg"
        img.convert("RGB").save(new_image_path, "JPEG")
        os.remove(image_path)
        image_path = new_image_path

    ocr_tokens = await extract_text_from_image(image_path)
    if not ocr_tokens:
        ocr_tokens = []

    mapping_result_dict = map_fields(ocr_tokens)
    mapping_output = build_field_mapping_output(mapping_result_dict)
    compliance_result = check_compliance(mapping_output)

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

    for v in compliance_result.violations:
        db.add(
            ViolationRecord(
                scan_id=scan_result.id,
                field_name=v.field_name,
                issue=v.issue,
                severity=v.severity,
                bbox=v.bbox.model_dump() if v.bbox is not None else None,
                legal_reference=v.legal_reference,
            )
        )

    await db.commit()
    await db.refresh(scan_result)

    return {
        "scan_id": str(scan_result.id),
        "status": scan_result.status,
        "image_path": scan_result.image_path,
        "is_compliant": compliance_result.is_compliant,
        "score": compliance_result.score,
        "violations": [
            {
                "field_name": v.field_name,
                "issue": v.issue,
                "severity": v.severity,
                "bbox": [v.bbox.xmin, v.bbox.ymin, v.bbox.xmax, v.bbox.ymax] if v.bbox else None,
                "legal_reference": v.legal_reference,
            }
            for v in compliance_result.violations
        ],
    }

@router.get("/scans/{scan_id}")
async def get_scan(
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(ScanResult)
        .options(selectinload(ScanResult.violations))
        .where(ScanResult.id == scan_id)
    )
    scan_result = result.scalar_one_or_none()

    if scan_result is None:
        raise HTTPException(status_code=404, detail="Scan not found")

    return {
        "id": str(scan_result.id),
        "product_id": str(scan_result.product_id) if scan_result.product_id else None,
        "image_path": scan_result.image_path,
        "status": scan_result.status,
        "is_compliant": scan_result.is_compliant,
        "compliance_score": scan_result.compliance_score,
        "raw_ocr": scan_result.raw_ocr,
        "extracted_fields": scan_result.extracted_fields,
        "created_at": scan_result.created_at.isoformat(),
        "violations": [
            {
                "id": str(v.id),
                "field_name": v.field_name,
                "issue": v.issue,
                "severity": v.severity,
                "bbox": v.bbox,
                "legal_reference": v.legal_reference,
                "created_at": v.created_at.isoformat(),
            }
            for v in scan_result.violations
        ],
    }


class FontCheckRequest(BaseModel):
    tap_x: int
    tap_y: int
    coin_key: str = "5_rupee"
    net_quantity_g_or_ml: Optional[float] = None


@router.post("/scans/{scan_id}/font-check")
async def font_check(
    scan_id: uuid.UUID,
    body: FontCheckRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    OPTIONAL, separate from the main automated pipeline (see Memory.md,
    Aug 29 entry). Requires a manually-supplied tap_point identifying the
    reference coin's approximate location -- there is no automatic coin
    detection yet. Never affects the scan's stored is_compliant/score;
    returns a best-effort result or a clear "unavailable" message.
    """
    result = await db.execute(select(ScanResult).where(ScanResult.id == scan_id))
    scan_result = result.scalar_one_or_none()

    if scan_result is None:
        raise HTTPException(status_code=404, detail="Scan not found")

    font_result = try_check_font_size(
        image_path=scan_result.image_path,
        ocr_tokens=scan_result.raw_ocr or [],
        tap_point=(body.tap_x, body.tap_y),
        coin_key=body.coin_key,
        net_quantity_g_or_ml=body.net_quantity_g_or_ml,
    )

    if font_result is None:
        return {
            "scan_id": str(scan_id),
            "available": False,
            "message": (
                "Font-size check could not be completed -- no coin "
                "detected near the given tap point, or the image could "
                "not be re-read. This does not affect the scan's main "
                "compliance result."
            ),
        }

    return {
        "scan_id": str(scan_id),
        "available": True,
        "result": font_result,
    }