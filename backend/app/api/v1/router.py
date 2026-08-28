from __future__ import annotations
import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
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
from app.services.rule_engine import check_compliance, FieldMappingOutput

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

    ocr_text = await extract_text_from_image(image_path)
    if not ocr_text:
        ocr_text = ""
    mapping_result_dict = map_fields(ocr_text)
    mapping_output = FieldMappingOutput(fields=mapping_result_dict)
    compliance_result = check_compliance(mapping_output)

    scan_result = ScanResult(
        id=uuid.uuid4(),
        product_id=None,
        image_path=image_path,
        status="completed",
        is_compliant=compliance_result.is_compliant,
        compliance_score=compliance_result.score,
        raw_ocr=ocr_text,
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
                bbox=v.bbox,
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
                "bbox": v.bbox,
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