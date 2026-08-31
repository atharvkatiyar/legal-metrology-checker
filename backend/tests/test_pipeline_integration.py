"""
Full-Stack Integration Lead — end-to-end skeleton test.
Exercises POST /api/v1/scans/init through the real FastAPI app with an
in-memory SQLite DB, asserting the whole chain (upload -> OCR -> field
mapping -> adapter -> rule engine -> persistence -> response) is wired
correctly. This does NOT re-test field_mapping's extraction accuracy
(that's covered by test_field_mapping.py / test_field_mapping_adversarial.py)
-- it only proves the modules are connected correctly end-to-end.
"""

import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.core.database import get_db
from app.models.schema import Base


@pytest.fixture
async def test_db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()
    await engine.dispose()


def _fake_image_bytes() -> bytes:
    img = Image.new("RGB", (400, 200), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# OCR tokens shaped exactly like app/services/ocr.py's real output
# (list of {"text", "bbox", "confidence", "language"} dicts), so this
# test exercises the true map_fields(list_of_tokens) code path rather
# than the plain-string path.
FAKE_OCR_TOKENS = [
    {"text": "MRP ₹249", "bbox": [[10, 10], [90, 10], [90, 30], [10, 30]],
     "confidence": 0.95, "language": "en"},
    {"text": "Net Qty 500 g", "bbox": [[10, 40], [110, 40], [110, 60], [10, 60]],
     "confidence": 0.93, "language": "en"},
    {"text": "Manufactured by ABC Foods Pvt Ltd, Pune",
     "bbox": [[10, 70], [220, 70], [220, 90], [10, 90]],
     "confidence": 0.91, "language": "en"},
    {"text": "Mfg Date 01/06/2026", "bbox": [[10, 100], [150, 100], [150, 120], [10, 120]],
     "confidence": 0.90, "language": "en"},
    {"text": "Consumer Care 1800-123-4567",
     "bbox": [[10, 130], [200, 130], [200, 150], [10, 150]],
     "confidence": 0.89, "language": "en"},
]


@pytest.mark.asyncio
async def test_full_pipeline_compliant_label(test_db_session):
    """A label with all 5 extractable mandatory fields present should be
    flagged non-compliant ONLY for country_of_origin (no extractor exists
    for it yet), never for the fields Field Mapping actually supports."""
    with patch(
        "app.api.v1.router.extract_text_from_image",
        new=AsyncMock(return_value=FAKE_OCR_TOKENS),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            files = {"image": ("label.jpg", _fake_image_bytes(), "image/jpeg")}
            response = await client.post("/api/v1/scans/init", files=files)

    assert response.status_code == 201, response.text
    data = response.json()

    assert "scan_id" in data
    assert data["status"] == "completed"

    violated_fields = {v["field_name"] for v in data["violations"]}
    assert "mrp" not in violated_fields, f"MRP wrongly flagged missing: {data['violations']}"
    assert "net_quantity" not in violated_fields
    assert "manufacturer" not in violated_fields
    assert "mfg_date" not in violated_fields
    assert "consumer_care" not in violated_fields
    # Documented known gap -- not a bug, see rule_engine.py MANDATORY_FIELDS comment.
    assert "country_of_origin" in violated_fields

    assert isinstance(data["score"], (int, float))
    assert 0.0 <= data["score"] <= 100.0

    # Fetch it back to prove persistence round-trips correctly.
    scan_id = data["scan_id"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_response = await client.get(f"/api/v1/scans/{scan_id}")
    assert get_response.status_code == 200
    fetched = get_response.json()
    assert fetched["id"] == scan_id
    assert fetched["extracted_fields"]["MRP"]["value"] == 249.0


@pytest.mark.asyncio
async def test_full_pipeline_missing_fields_label(test_db_session):
    """A label with NO extractable fields should flag all 6 mandatory
    fields as violations and score should reflect that via deductions."""
    with patch(
        "app.api.v1.router.extract_text_from_image",
        new=AsyncMock(return_value=[]),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            files = {"image": ("blank.jpg", _fake_image_bytes(), "image/jpeg")}
            response = await client.post("/api/v1/scans/init", files=files)

    assert response.status_code == 201, response.text
    data = response.json()

    assert data["is_compliant"] is False
    violated_fields = {v["field_name"] for v in data["violations"]}
    assert violated_fields == {
        "mrp", "net_quantity", "manufacturer",
        "mfg_date", "consumer_care", "country_of_origin",
    }
    assert data["score"] < 100.0


@pytest.mark.asyncio
async def test_full_pipeline_ocr_failure_does_not_crash(test_db_session):
    """extract_text_from_image returning [] on internal failure (its own
    documented fallback) must not crash the route -- it should produce a
    completed scan with all fields missing, not a 500."""
    with patch(
        "app.api.v1.router.extract_text_from_image",
        new=AsyncMock(return_value=[]),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            files = {"image": ("corrupt.jpg", b"not a real image but has bytes", "image/jpeg")}
            response = await client.post("/api/v1/scans/init", files=files)

    assert response.status_code == 201
    assert response.json()["status"] == "completed"