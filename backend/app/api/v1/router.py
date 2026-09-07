# app/api/v1/router.py
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
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from passlib.context import CryptContext

from app.core.config import settings
from app.core.database import get_db
from app.models.schema import ScanResult, ViolationRecord, User, FontCheckRecord
from app.schemas.contracts import (
    FontCheckInitResponse,
    FontCheckCalibrateRequest,
    FontCheckCalibrateResponse,
    FontCheckHistoryResponse,
    FontCheckHistoryItem,
)
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
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Blocking I/O helpers
# ---------------------------------------------------------------------------

def _write_bytes_sync(path: str, contents: bytes) -> None:
    with open(path, "wb") as f:
        f.write(contents)


def _process_image_sync(image_path: str) -> str:
    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img)

    if image_path.lower().endswith((".heic", ".heif")):
        new_image_path = os.path.splitext(image_path)[0] + ".jpg"
        img.convert("RGB").save(new_image_path, "JPEG")
        os.remove(image_path)
        return new_image_path

    img.save(image_path)
    return image_path


async def _save_and_normalize_upload(upload: UploadFile) -> str:
    file_extension = os.path.splitext(upload.filename or "")[1] or ".jpg"
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    image_path = os.path.join(UPLOAD_DIR, unique_filename)

    contents = await upload.read()
    await upload.close()

    await asyncio.to_thread(_write_bytes_sync, image_path, contents)

    try:
        image_path = await asyncio.to_thread(_process_image_sync, image_path)
    except Exception as e:
        logger.warning(f"EXIF rotation/normalization failed for {image_path}: {e}")

    return image_path


# ---------------------------------------------------------------------------
# Field merge helpers
# ---------------------------------------------------------------------------

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


def _strip_internal_keys(mapping_result: dict[str, Any]) -> dict[str, Any]:
    # "_image_index" is deliberately kept: font_check() needs it after
    # reload from the DB to know which physical photo each field's bbox
    # is relative to (see its bbox_counts auto-select logic). Every other
    # leading-underscore key is still stripped as before.
    cleaned: dict[str, Any] = {}
    for field_name, field_result in mapping_result.items():
        if isinstance(field_result, dict):
            cleaned[field_name] = {
                k: v for k, v in field_result.items()
                if not k.startswith("_") or k == "_image_index"
            }
        else:
            cleaned[field_name] = field_result
    return cleaned


def _scope_mapping_to_primary_image(
    merged_mapping_result: dict[str, Any],
    primary_image_index: int,
) -> dict[str, Any]:
    scoped: dict[str, Any] = {}
    for field_name, field_result in merged_mapping_result.items():
        if not isinstance(field_result, dict):
            scoped[field_name] = field_result
            continue
        source_index = field_result.get("_image_index")
        if source_index is not None and source_index != primary_image_index:
            scoped_field = dict(field_result)
            scoped_field["bbox"] = None
            scoped[field_name] = scoped_field
        else:
            scoped[field_name] = field_result
    return scoped


async def _process_single_image(idx: int, path: str) -> dict[str, Any]:
    try:
        ocr_tokens = await extract_text_from_image(path)
    except Exception:
        logger.exception("OCR failed for image %s", path)
        ocr_tokens = []
    if not isinstance(ocr_tokens, list):
        ocr_tokens = []

    text = " ".join(
        t.get("text", "") for t in ocr_tokens if isinstance(t, dict)
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

    return {
        "idx": idx,
        "path": path,
        "ocr_tokens": ocr_tokens,
        "text": text,
        "mapping_result": image_mapping_result,
    }


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def health_check() -> dict:
    return {
        "status": "healthy",
        "timestamp": utcnow().isoformat(),
    }


@router.get("/uploads/{filename}")
async def get_uploaded_image(filename: str):
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)


class LoginRequest(BaseModel):
    login_identifier: str
    password: str


@router.post("/login")
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(User).where(
            or_(
                getattr(User, "email", User.officer_id) == body.login_identifier,
                User.officer_id == body.login_identifier
            )
        )
    )
    user = result.scalar_one_or_none()
    if user is None or not pwd_context.verify(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Government Credentials. Access Denied.",
        )
    return {
        "officer": {
            "id": str(user.id),
            "name": user.name,
            "officer_id": user.officer_id,
            "gov_id": getattr(user, "gov_id_number", "N/A"),
            "jurisdiction": getattr(user, "jurisdiction_circle", "N/A"),
            "role": user.role,
        }
    }


@router.get("/scans/history")
async def get_scan_history(
    limit: int = 25,
    offset: int = 0,
    officer_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    query = select(ScanResult)

    if officer_id:
        try:
            parsed_uuid = uuid.UUID(officer_id)
            query = query.where(ScanResult.officer_id == parsed_uuid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid officer ID format")

    query = query.order_by(ScanResult.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    scans = result.scalars().all()

    items = []
    for scan in scans:
        items.append({
            "id": str(scan.id),
            "created_at": scan.created_at.isoformat(),
            "is_compliant": scan.is_compliant,
            "compliance_score": scan.compliance_score,
            "product_id": str(scan.product_id) if scan.product_id else "Unregistered Product",
        })
    return {"items": items}


@router.get("/scans/metrics")
async def get_scan_metrics(
    officer_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    total_query = select(func.count()).select_from(ScanResult)
    compliant_query = select(func.count()).select_from(ScanResult).where(ScanResult.is_compliant == True)  # noqa: E712
    violation_query = select(
        ViolationRecord.field_name,
        func.count(ViolationRecord.field_name).label("violation_count"),
    ).select_from(ViolationRecord)

    if officer_id:
        try:
            parsed_uuid = uuid.UUID(officer_id)
            total_query = total_query.where(ScanResult.officer_id == parsed_uuid)
            compliant_query = compliant_query.where(ScanResult.officer_id == parsed_uuid)
            violation_query = violation_query.join(
                ScanResult, ViolationRecord.scan_id == ScanResult.id
            ).where(ScanResult.officer_id == parsed_uuid)
        except ValueError:
            pass

    total_result = await db.execute(total_query)
    total = total_result.scalar() or 0

    if total == 0:
        return {
            "total": 0,
            "pass_rate": 0.0,
            "top_violation": "None",
        }

    compliant_result = await db.execute(compliant_query)
    compliant_count = compliant_result.scalar() or 0
    pass_rate = round((compliant_count / total) * 100, 1)

    violation_query = violation_query.group_by(ViolationRecord.field_name).order_by(
        func.count(ViolationRecord.field_name).desc()
    ).limit(1)
    top_violation_result = await db.execute(violation_query)
    top_violation_row = top_violation_result.first()
    top_violation = top_violation_row[0] if top_violation_row else "None"

    return {
        "total": total,
        "pass_rate": pass_rate,
        "top_violation": top_violation,
    }


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

    per_image_task_results = await asyncio.gather(
        *(_process_single_image(idx, path) for idx, path in enumerate(image_paths))
    )
    per_image_task_results.sort(key=lambda r: r["idx"])

    per_image_ocr: list[list[dict[str, Any]]] = [r["ocr_tokens"] for r in per_image_task_results]
    per_image_texts: list[str] = [r["text"] for r in per_image_task_results]
    per_image_raw_results: list[dict[str, Any]] = [r["mapping_result"] for r in per_image_task_results]

    merged_raw_results = _merge_mapping_results(per_image_raw_results)


    # -------------------------------------------

    field_to_image_index: dict[str, Optional[int]] = {}
    for source_key, target_key in _FIELD_KEY_MAP.items():
        source_result = merged_raw_results.get(source_key)
        field_to_image_index[target_key] = (
            source_result.get("_image_index")
            if isinstance(source_result, dict)
            else None
        )

    mapping_output = build_field_mapping_output(merged_raw_results)
    compliance_result = check_compliance(mapping_output)
    persisted_fields = _strip_internal_keys(merged_raw_results)
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
        extracted_fields=persisted_fields,
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
        "extracted_fields": persisted_fields,
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
        "product_id": str(scan_result.product_id) if scan_result.product_id else None,
        "officer_id": str(scan_result.officer_id) if scan_result.officer_id else None,
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
                "violation_category": getattr(v, "violation_category", None),
                "measured_value": getattr(v, "measured_value", None),
                "bbox": v.bbox,
                "legal_reference": v.legal_reference,
                "created_at": v.created_at.isoformat(),
            }
            for v in scan_result.violations
        ],
    }


# ---------------------------------------------------------------------------
# Manual Override Endpoint (Matrix Submission)
# ---------------------------------------------------------------------------

class RuleOverride(BaseModel):
    rule_key: str
    status: str
    evidence: str

class OverrideRequest(BaseModel):
    score: int
    is_compliant: bool
    overrides: list[RuleOverride]

@router.post("/scans/{scan_id}/override")
async def override_scan(
    scan_id: uuid.UUID,
    body: OverrideRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(ScanResult)
        .options(selectinload(ScanResult.violations))
        .where(ScanResult.id == scan_id)
    )
    scan = result.scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    scan.compliance_score = body.score
    scan.is_compliant = body.is_compliant

    current_violations = {v.field_name: v for v in scan.violations}
    
    extracted = scan.extracted_fields or {}
    if not isinstance(extracted, dict):
        extracted = {}

    for over in body.overrides:
        if over.status == "PASS":
            if over.rule_key in current_violations:
                await db.delete(current_violations[over.rule_key])
        elif over.status == "FAIL":
            if over.rule_key not in current_violations:
                new_v = ViolationRecord(
                    scan_id=scan.id,
                    field_name=over.rule_key,
                    issue=over.evidence,
                    severity="HIGH",
                    legal_reference="Rule 6/9",
                )
                db.add(new_v)
            else:
                current_violations[over.rule_key].issue = over.evidence

        if over.rule_key not in extracted:
            extracted[over.rule_key] = {}
        if isinstance(extracted[over.rule_key], dict):
            extracted[over.rule_key]["confidence"] = "high" if over.status == "PASS" else "none"
            extracted[over.rule_key]["value"] = over.evidence if over.status == "PASS" else None
            extracted[over.rule_key]["_manual_override"] = over.status

    # Re-assign to force SQLAlchemy JSON column update
    scan.extracted_fields = extracted
    
    await db.commit()
    return {"message": "Success"}


async def _load_scan_for_pdf(scan_id_filter, db: AsyncSession) -> ScanResult:
    result = await db.execute(
        select(ScanResult)
        .options(selectinload(ScanResult.violations))
        .where(scan_id_filter)
    )
    scan = result.scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return scan


async def _resolve_address_and_officer(
    scan: ScanResult, db: AsyncSession
) -> tuple[Optional[str], Optional[str]]:
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

    officer_name: Optional[str] = None
    if scan.officer_id is not None:
        officer_result = await db.execute(
            select(User).where(User.id == scan.officer_id)
        )
        officer_user = officer_result.scalar_one_or_none()
        if officer_user is not None:
            officer_name = f"{officer_user.name}, {officer_user.role} ({officer_user.officer_id})"

    return resolved_address, officer_name


@router.get("/scans/{scan_id}/pdf")
async def get_scan_pdf(
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    scan = await _load_scan_for_pdf(ScanResult.id == scan_id, db)
    resolved_address, officer_name = await _resolve_address_and_officer(scan, db)

    cr_no = f"CR-{scan.created_at.year}-{str(scan.id)[:8].upper()}"
    pdf_bytes = generate_inspection_certificate_pdf(
        scan=scan,
        violations=list(scan.violations),
        cr_no=cr_no,
        resolved_address=resolved_address,
        officer_name=officer_name,
    )
    filename = f"lmcs-certificate-{str(scan.id)[:8]}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/scans/public/{cert_id}/pdf")
async def get_public_scan_pdf(
    cert_id: str,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    clean_id = cert_id.upper().replace("CERT-", "").replace("CR-", "").strip()

    scan = await _load_scan_for_pdf(
        cast(ScanResult.id, String).ilike(f"{clean_id}%"), db
    )
    resolved_address, officer_name = await _resolve_address_and_officer(scan, db)

    cr_no = f"CR-{scan.created_at.year}-{str(scan.id)[:8].upper()}"
    pdf_bytes = generate_inspection_certificate_pdf(
        scan=scan,
        violations=list(scan.violations),
        cr_no=cr_no,
        resolved_address=resolved_address,
        officer_name=officer_name,
    )
    filename = f"lmcs-certificate-{clean_id}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class FontCheckRequest(BaseModel):
    tap_x: int
    tap_y: int
    coin_key: str = "5_rupee"
    net_quantity_g_or_ml: Optional[float] = None
    image_index: Optional[int] = None


@router.post("/scans/{scan_id}/font-check")
async def font_check(
    scan_id: uuid.UUID,
    body: FontCheckRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(ScanResult).where(ScanResult.id == scan_id))
    scan_result = result.scalar_one_or_none()
    if scan_result is None:
        raise HTTPException(status_code=404, detail="Scan not found")

    try:
        stored_paths = json.loads(scan_result.image_path)
        if not isinstance(stored_paths, list) or not stored_paths:
            stored_paths = [scan_result.image_path]
    except (TypeError, ValueError):
        stored_paths = [scan_result.image_path]

    extracted_fields = scan_result.extracted_fields or {}
    if not isinstance(extracted_fields, dict):
        extracted_fields = {}

    # Use body.image_index if the officer explicitly picked an angle;
    # otherwise auto-select whichever image actually has the most fields
    # with a non-null bbox, rather than blindly trusting stored_paths[0].
    # A field's bbox only survives _scope_mapping_to_primary_image() for
    # the image it was actually extracted from -- so counting non-null
    # bboxes per candidate index tells us which physical photo is worth
    # measuring against.
    if body.image_index is not None and 0 <= body.image_index < len(stored_paths):
        primary_index = body.image_index
    else:
        bbox_counts: dict[int, int] = {i: 0 for i in range(len(stored_paths))}
        for field_result in extracted_fields.values():
            if not isinstance(field_result, dict):
                continue
            src_idx = field_result.get("_image_index")
            if field_result.get("bbox") is not None and src_idx in bbox_counts:
                bbox_counts[src_idx] += 1
        primary_index = max(bbox_counts, key=bbox_counts.get) if bbox_counts else 0

    primary_image_path = stored_paths[primary_index]
    scoped_mapping_result = _scope_mapping_to_primary_image(
        extracted_fields, primary_image_index=primary_index
    )

    try:
        font_result = try_check_font_size(
            image_path=primary_image_path,
            mapping_result_dict=scoped_mapping_result,
            tap_point=(body.tap_x, body.tap_y),
            coin_key=body.coin_key,
            net_quantity_g_or_ml=body.net_quantity_g_or_ml,
        )
    except Exception:
        logger.exception(
            "Font-size check raised an unexpected exception for scan_id=%s",
            scan_id,
        )
        return {
            "scan_id": str(scan_id),
            "available": False,
            "message": (
                "Font-size check could not be completed due to an internal "
                "error. This does not affect the scan's main compliance "
                "result -- try tapping closer to the center of the coin."
            ),
        }

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

    violations_found = font_result.get("violations") or []
    checked_fields = (font_result.get("value") or {}).get("checked_fields") or []
    is_compliant = len(checked_fields) > 0 and len(violations_found) == 0

    if not is_compliant and violations_found:
        for v in violations_found:
            new_violation = ViolationRecord(
                scan_id=scan_id,
                violation_category="FONT_SIZE",
                field_name=str(v.get("field", "net_quantity")).lower(),
                issue=(
                    f"Text height is {v.get('measured_mm')}mm, "
                    f"below the required minimum of {v.get('required_mm')}mm."
                ),
                severity="HIGH",
                measured_value=f"{v.get('measured_mm')}mm",
                legal_reference="Rule 9",
            )
            scan_result.is_compliant = False
            scan_result.compliance_score = max(0, (scan_result.compliance_score or 100) - 20)
            db.add(new_violation)
        await db.commit()

    return {
        "scan_id": str(scan_id),
        "available": True,
        "primary_image_index": primary_index,
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

    sync_url = f"{settings.SUPABASE_URL}/rest/v1/{settings.SUPABASE_SYNC_TABLE}"
    headers = {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    if not settings.SUPABASE_SERVICE_KEY:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_SERVICE_KEY is not configured; cannot sync to cloud backend.",
        )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(sync_url, json=payload, headers=headers, timeout=30.0)
    except httpx.HTTPError:
        logger.exception("Network error while syncing scans to cloud backend")
        raise HTTPException(
            status_code=502,
            detail="Failed to sync scans to cloud backend.",
        )

    if response.status_code not in (200, 201):
        logger.error(
            "Supabase sync failed: status=%s body=%s", response.status_code, response.text
        )
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
@router.post(
    "/font-checks/init",
    status_code=status.HTTP_201_CREATED,
    response_model=FontCheckInitResponse,
)
async def init_font_check(
    image: UploadFile = File(...),
    officer_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
) -> FontCheckInitResponse:
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    parsed_officer_id: Optional[uuid.UUID] = None
    if officer_id:
        try:
            parsed_officer_id = uuid.UUID(officer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="officer_id must be a valid UUID")

    try:
        image_path = await _save_and_normalize_upload(image)
    except Exception:
        logger.exception("Failed to save/normalize font-check upload %s", image.filename)
        raise HTTPException(status_code=400, detail="Could not process uploaded image.")

    try:
        with Image.open(image_path) as im:
            width, height = im.size
    except Exception:
        logger.exception("Failed to read dimensions for %s", image_path)
        raise HTTPException(status_code=400, detail="Uploaded file is not a readable image.")

    record = FontCheckRecord(
        id=uuid.uuid4(),
        officer_id=parsed_officer_id,
        image_path=image_path,
        coin_key="5_rupee",
        tap_x=None,
        tap_y=None,
        net_quantity_g_or_ml=None,
        measured_mm=None,
        required_mm=None,
        is_compliant=None,
        raw_result=None,
        created_at=utcnow(),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return FontCheckInitResponse(
        record_id=str(record.id),
        image_url=f"/api/v1/uploads/{os.path.basename(image_path)}",
        image_width=width,
        image_height=height,
    )
@router.post(
    "/font-checks/{record_id}/calibrate",
    response_model=FontCheckCalibrateResponse,
)
async def calibrate_font_check(
    record_id: uuid.UUID,
    body: FontCheckCalibrateRequest,
    db: AsyncSession = Depends(get_db),
) -> FontCheckCalibrateResponse:
    result = await db.execute(
        select(FontCheckRecord).where(FontCheckRecord.id == record_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Font check record not found")

    try:
        ocr_tokens = await extract_text_from_image(record.image_path)
    except Exception:
        logger.exception("OCR failed during font-check calibration for %s", record.image_path)
        ocr_tokens = []
    if not isinstance(ocr_tokens, list):
        ocr_tokens = []

    try:
        mapping_result = await map_fields_with_fallback(ocr_tokens, image_path=record.image_path)
    except Exception:
        logger.exception("Field mapping failed during font-check calibration for %s", record.image_path)
        mapping_result = {}
    if not isinstance(mapping_result, dict):
        mapping_result = {}

    font_result = try_check_font_size(
        image_path=record.image_path,
        mapping_result_dict=mapping_result,
        tap_point=(body.tap_x, body.tap_y),
        coin_key=body.coin_key,
        net_quantity_g_or_ml=body.net_quantity_g_or_ml,
    )

    record.coin_key = body.coin_key
    record.tap_x = body.tap_x
    record.tap_y = body.tap_y
    record.net_quantity_g_or_ml = body.net_quantity_g_or_ml

    if font_result is None:
        record.raw_result = None
        record.is_compliant = None
        await db.commit()
        return FontCheckCalibrateResponse(
            record_id=str(record.id),
            available=False,
            message=(
                "Font-size check could not be completed -- "
                "no coin detected near the given tap point, "
                "or the image could not be re-read."
            ),
        )

    violations = font_result.get("violations") or []
    checked_fields = (font_result.get("value") or {}).get("checked_fields") or []

    if violations:
        summary_entry = violations[0]
    elif checked_fields:
        summary_entry = checked_fields[0]
    else:
        summary_entry = {}

    record.measured_mm = summary_entry.get("measured_mm")
    record.required_mm = summary_entry.get("required_mm")
    record.is_compliant = len(violations) == 0
    record.raw_result = font_result

    await db.commit()

    return FontCheckCalibrateResponse(
        record_id=str(record.id),
        available=True,
        result=font_result,
    )
@router.get("/font-checks/history", response_model=FontCheckHistoryResponse)
async def get_font_check_history(
    limit: int = 25,
    offset: int = 0,
    officer_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> FontCheckHistoryResponse:
    query = select(FontCheckRecord).where(FontCheckRecord.raw_result.is_not(None))

    if officer_id:
        try:
            parsed_uuid = uuid.UUID(officer_id)
            query = query.where(FontCheckRecord.officer_id == parsed_uuid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid officer ID format")

    query = query.order_by(FontCheckRecord.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    records = result.scalars().all()

    count_query = select(func.count()).select_from(FontCheckRecord).where(
        FontCheckRecord.raw_result.is_not(None)
    )
    total = (await db.execute(count_query)).scalar() or 0

    items = [
        FontCheckHistoryItem(
            record_id=str(r.id),
            created_at=r.created_at.isoformat(),
            is_compliant=r.is_compliant,
            measured_mm=r.measured_mm,
            required_mm=r.required_mm,
            coin_key=r.coin_key,
            image_url=f"/api/v1/uploads/{os.path.basename(r.image_path)}",
        )
        for r in records
    ]

    return FontCheckHistoryResponse(total=total, items=items)