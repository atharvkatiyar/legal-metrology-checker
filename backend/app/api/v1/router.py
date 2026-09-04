from __future__ import annotations
import asyncio
import io
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.schema import ScanResult, ViolationRecord, User
from app.services.geocoding import reverse_geocode
from app.services.pdf_generator import generate_inspection_certificate_pdf

from PIL import Image, ImageOps
import pillow_heif
pillow_heif.register_heif_opener()

from app.services.ocr import extract_text_from_image
from app.services.field_mapping_fallback import map_fields_with_fallback
from app.services.field_mapping_adapter import build_field_mapping_output, _FIELD_KEY_MAP
from app.services.rule_engine import check_compliance
from app.services.font_size_adapter import try_check_font_size

logger = logging.getLogger(__name__)

router = APIRouter()
UPLOAD_DIR = "uploads"
_CONFIDENCE_RANK = {"high": 2, "low": 1, "none": 0}

SUPABASE_SYNC_URL = "https://gthsgretafamflrkcdhd.supabase.co/rest/v1/cloud_scan_results"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _save_and_normalize_upload(upload: UploadFile) -> str:
    file_extension = os.path.splitext(upload.filename or "")[1] or ".jpg"
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    image_path = os.path.join(UPLOAD_DIR, unique_filename)

    contents = await upload.read()
    with open(image_path, "wb") as f:
        f.write(contents)
    await upload.close()

    try:
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)
        
        if image_path.lower().endswith((".heic", ".heif")):
            new_image_path = os.path.splitext(image_path)[0] + ".jpg"
            img.convert("RGB").save(new_image_path, "JPEG")
            os.remove(image_path)
            image_path = new_image_path
        else:
            img.save(image_path)
    except Exception as e:
        logger.warning(f"EXIF rotation failed for {image_path}: {e}")

    return image_path


def _merge_field_result(
    existing: Optional[dict[str, Any]],
    candidate: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if existing is None:
        return candidate
    if candidate is None:
        return existing
    if existing.get("value") is None and candidate.get("value") is not None:
        return candidate
    if candidate.get("value") is None:
        return existing
    existing_rank = _CONFIDENCE_RANK.get(existing.get("confidence"), 0)
    candidate_rank = _CONFIDENCE_RANK.get(candidate.get("confidence"), 0)
    if candidate_rank > existing_rank:
        return candidate
    return existing


def _merge_mapping_results(
    per_image_results: list[dict[str, Any]],
) -> dict[str, Any]:
    if not per_image_results:
        return {}
    field_names: set[str] = set()
    for result in per_image_results:
        field_names.update(result.keys())
    merged: dict[str, Any] = {}
    for field_name in field_names:
        current: Optional[dict[str, Any]] = None
        for result in per_image_results:
            candidate = result.get(field_name)
            if not isinstance(candidate, dict):
                continue
            current = _merge_field_result(current, candidate)
        merged[field_name] = current if current is not None else {
            "field": field_name,
            "value": None,
            "confidence": "none",
            "raw_evidence": None,
            "ambiguous": False,
            "all_candidates": [],
        }
    return merged


@router.get("/health")
async def health_check() -> dict:
    return {
        "status": "healthy",
        "timestamp": utcnow().isoformat(),
    }


class LoginRequest(BaseModel):
    officer_id: str
    password: str


@router.post("/login")
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(User).where(User.officer_id == body.officer_id)
    )
    user = result.scalar_one_or_none()

    if user is None or user.hashed_password != body.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid officer ID or password",
        )

    return {
        "user_id": str(user.id),
        "officer_id": user.officer_id,
        "role": user.role,
    }


@router.get("/scans/history")
async def get_scan_history(
    limit: int = 25,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
) -> dict:
    query = select(ScanResult).order_by(ScanResult.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    scans = result.scalars().all()
    items = []
    for scan in scans:
        items.append({
            "id": str(scan.id),
            "created_at": scan.created_at.isoformat(),
            "is_compliant": scan.is_compliant,
            "compliance_score": scan.compliance_score,
            "product_id": str(scan.product_id) if scan.product_id else "Unregistered Product"
        })
    return {"items": items}


@router.post(
    "/scans/init",
    status_code=status.HTTP_201_CREATED,
)
async def init_scan(
    images: list[UploadFile] = File(...),
    officer_id: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    parsed_officer_id: Optional[uuid.UUID] = None
    if officer_id:
        try:
            parsed_officer_id = uuid.UUID(officer_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="officer_id must be a valid UUID",
            )

    image_paths: list[str] = []
    for upload in images:
        try:
            path = await _save_and_normalize_upload(upload)
        except Exception:
            logger.exception(
                "Failed to save/normalize uploaded image %s",
                upload.filename,
            )
            continue
        image_paths.append(path)
    
    if not image_paths:
        raise HTTPException(
            status_code=400,
            detail="No valid images were uploaded.",
        )
        
    per_image_ocr: list[list[dict[str, Any]]] = []
    per_image_texts: list[str] = []
    per_image_raw_results: list[dict[str, Any]] = []
    
    for idx, path in enumerate(image_paths):
        try:
            ocr_tokens = await extract_text_from_image(path)
        except Exception:
            logger.exception("OCR failed for image %s", path)
            ocr_tokens = []
        if not ocr_tokens:
            ocr_tokens = []
        per_image_ocr.append(ocr_tokens)
        per_image_texts.append(
            " ".join(
                t.get("text", "")
                for t in ocr_tokens
                if isinstance(t, dict)
            )
        )
        try:
            image_mapping_result = await map_fields_with_fallback(
                ocr_tokens,
                image_path=path,
            )
        except Exception:
            logger.exception("Field mapping failed for image %s", path)
            image_mapping_result = {}
        if not isinstance(image_mapping_result, dict):
            image_mapping_result = {}
        for field_result in image_mapping_result.values():
            if isinstance(field_result, dict):
                field_result["_image_index"] = idx
        per_image_raw_results.append(image_mapping_result)
        
    merged_raw_results = _merge_mapping_results(per_image_raw_results)
    mapping_output = build_field_mapping_output(merged_raw_results)
    compliance_result = check_compliance(mapping_output)
    
    field_to_image_index: dict[str, Optional[int]] = {}
    for source_key, target_key in _FIELD_KEY_MAP.items():
        source_result = merged_raw_results.get(source_key)
        field_to_image_index[target_key] = (
            source_result.get("_image_index")
            if isinstance(source_result, dict)
            else None
        )
        
    combined_raw_text = "\n\n".join(per_image_texts)
    
    scan_result = ScanResult(
        id=uuid.uuid4(),
        product_id=None,
        officer_id=parsed_officer_id,
        image_path=json.dumps(image_paths),
        status="completed",
        is_compliant=compliance_result.is_compliant,
        compliance_score=compliance_result.score,
        sync_status="pending_sync",
        raw_ocr={
            "combined_text": combined_raw_text,
            "images": [
                {"image_path": p, "tokens": tokens}
                for p, tokens in zip(image_paths, per_image_ocr)
            ],
        },
        extracted_fields=merged_raw_results,
        latitude=latitude,
        longitude=longitude,
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
    await db.refresh(scan_result)
    
    return {
        "scan_id": str(scan_result.id),
        "status": scan_result.status,
        "officer_id": str(scan_result.officer_id) if scan_result.officer_id else None,
        "sync_status": scan_result.sync_status,
        "image_paths": image_paths,
        "is_compliant": compliance_result.is_compliant,
        "score": compliance_result.score,
        "extracted_fields": merged_raw_results,
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
                "image_index": field_to_image_index.get(violation.field_name),
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
        .options(selectinload(ScanResult.violations))
        .where(ScanResult.id == scan_id)
    )
    scan_result = result.scalar_one_or_none()
    if scan_result is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    try:
        image_paths = json.loads(scan_result.image_path)
        if not isinstance(image_paths, list):
            image_paths = [scan_result.image_path]
    except (TypeError, ValueError):
        image_paths = [scan_result.image_path]
    return {
        "id": str(scan_result.id),
        "product_id": (
            str(scan_result.product_id)
            if scan_result.product_id
            else None
        ),
        "officer_id": (
            str(scan_result.officer_id)
            if scan_result.officer_id
            else None
        ),
        "sync_status": scan_result.sync_status,
        "image_paths": image_paths,
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
                "violation_category": v.violation_category,
                "measured_value": v.measured_value,
                "bbox": v.bbox,
                "legal_reference": v.legal_reference,
                "created_at": v.created_at.isoformat(),
            }
            for v in scan_result.violations
        ],
    }


@router.get("/scans/{scan_id}/pdf")
async def get_scan_pdf(
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    result = await db.execute(
        select(ScanResult)
        .options(selectinload(ScanResult.violations))
        .where(ScanResult.id == scan_id)
    )
    scan = result.scalar_one_or_none()

    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")

    resolved_address = scan.location_address
    if (
        resolved_address is None
        and scan.latitude is not None
        and scan.longitude is not None
    ):
        resolved_address = await asyncio.to_thread(
            reverse_geocode, scan.latitude, scan.longitude
        )
        if resolved_address:
            scan.location_address = resolved_address
            await db.commit()
            await db.refresh(scan)

    cr_no = f"CR-{scan.created_at.year}-{str(scan.id)[:8].upper()}"

    pdf_bytes = generate_inspection_certificate_pdf(
        scan=scan,
        violations=list(scan.violations),
        cr_no=cr_no,
        resolved_address=resolved_address,
    )

    filename = f"lmcs-certificate-{str(scan.id)[:8]}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


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
    result = await db.execute(
        select(ScanResult).where(ScanResult.id == scan_id)
    )
    scan_result = result.scalar_one_or_none()
    if scan_result is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    try:
        stored_paths = json.loads(scan_result.image_path)
        if isinstance(stored_paths, list) and stored_paths:
            primary_image_path = stored_paths[0]
        else:
            primary_image_path = scan_result.image_path
    except (TypeError, ValueError):
        primary_image_path = scan_result.image_path
    raw_ocr = scan_result.raw_ocr or {}
    if isinstance(raw_ocr, dict) and "images" in raw_ocr:
        images_meta = raw_ocr.get("images") or []
        primary_tokens = images_meta[0]["tokens"] if images_meta else []
    elif isinstance(raw_ocr, list):
        primary_tokens = raw_ocr
    else:
        primary_tokens = []
    font_result = try_check_font_size(
        image_path=primary_image_path,
        ocr_tokens=primary_tokens,
        tap_point=(body.tap_x, body.tap_y),
        coin_key=body.coin_key,
        net_quantity_g_or_ml=body.net_quantity_g_or_ml,
    )

    if font_result is not None and font_result.get("is_compliant") is False:
        new_violation = ViolationRecord(
            scan_id=scan_id,
            violation_category="FONT_SIZE",
            field_name="net_quantity",
            issue=(
                f"Text height is {font_result['measured_height_mm']}mm, "
                f"which is below the required minimum."
            ),
            severity="HIGH",
            measured_value=f"{font_result['measured_height_mm']}mm",
            legal_reference="Rule 9",
        )
        scan_result.is_compliant = False
        scan_result.compliance_score = max(
            0, (scan_result.compliance_score or 100) - 20
        )
        db.add(new_violation)
        await db.commit()

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


@router.post("/scans/sync")
async def sync_scans(
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(ScanResult).where(ScanResult.sync_status == "pending_sync")
    )
    pending_scans = result.scalars().all()

    if not pending_scans:
        return {"message": "No pending scans to sync.", "synced_count": 0}

    payload = [
        {
            "id": str(scan.id),
            "officer_id": str(scan.officer_id) if scan.officer_id else None,
            "is_compliant": scan.is_compliant,
            "score": scan.compliance_score,
            "created_at": scan.created_at.isoformat(),
        }
        for scan in pending_scans
    ]

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(SUPABASE_SYNC_URL, json=payload)
    except httpx.HTTPError:
        logger.exception("Network error while syncing scans to cloud backend")
        raise HTTPException(
            status_code=502,
            detail="Failed to sync scans to cloud backend.",
        )

    if response.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail="Failed to sync scans to cloud backend.",
        )

    for scan in pending_scans:
        scan.sync_status = "synced"

    await db.commit()

    return {
        "message": "Sync completed successfully.",
        "synced_count": len(pending_scans),
    }