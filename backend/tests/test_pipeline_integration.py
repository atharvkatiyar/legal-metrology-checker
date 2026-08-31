from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


# ---------------------------------------------------------------------------
# Fake OCR tokens
# ---------------------------------------------------------------------------
#
# These tokens intentionally use the labels that the current Field Mapping
# implementation recognizes. This makes the integration test exercise the
# real resolver logic rather than asking it to infer unlabeled values.
#

FAKE_OCR_TOKENS = [
    {
        "text": "MRP Rs. 100",
        "bbox": [[0, 0], [100, 0], [100, 20], [0, 20]],
        "confidence": 0.99,
        "language": "en",
    },
    {
        "text": "Net Qty 500 g",
        "bbox": [[0, 30], [120, 30], [120, 50], [0, 50]],
        "confidence": 0.99,
        "language": "en",
    },
    {
        "text": (
            "Manufactured by ABC Company, Jaipur, Rajasthan"
        ),
        "bbox": [[0, 60], [260, 60], [260, 80], [0, 80]],
        "confidence": 0.99,
        "language": "en",
    },
    {
        "text": "Mfg Date 01/01/2026",
        "bbox": [[0, 90], [160, 90], [160, 110], [0, 110]],
        "confidence": 0.99,
        "language": "en",
    },
    {
        "text": (
            "Customer Care 1800-123-4567"
        ),
        "bbox": [[0, 120], [220, 120], [220, 140], [0, 140]],
        "confidence": 0.99,
        "language": "en",
    },
]


# ---------------------------------------------------------------------------
# Fake image
# ---------------------------------------------------------------------------

def _fake_image_bytes() -> bytes:
    """
    Minimal JPEG-like payload.

    The API only needs file bytes for this integration test because the OCR
    function is mocked.
    """
    return (
        b"\xff\xd8\xff\xe0"
        b"\x00\x10JFIF"
        b"\x00\x01\x02\x00"
        b"\x00\x01\x00\x01"
        b"\x00\x00\xff\xd9"
    )


# ---------------------------------------------------------------------------
# Compliant-label pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_compliant_label():
    """
    A label with all five currently supported Field Mapping fields present
    should not be flagged missing.

    country_of_origin is intentionally excluded because the current
    Field Mapping/rule-engine implementation does not track it yet.
    """
    with patch(
        "app.api.v1.router.extract_text_from_image",
        new=AsyncMock(
            return_value=FAKE_OCR_TOKENS
        ),
    ):
        transport = ASGITransport(app=app)

        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            files = {
                "image": (
                    "label.jpg",
                    _fake_image_bytes(),
                    "image/jpeg",
                )
            }

            response = await client.post(
                "/api/v1/scans/init",
                files=files,
            )

    assert response.status_code == 201, response.text

    data = response.json()

    assert "scan_id" in data
    assert data["status"] == "completed"

    violated_fields = {
        violation["field_name"]
        for violation in data["violations"]
    }

    assert "mrp" not in violated_fields
    assert "net_quantity" not in violated_fields
    assert "manufacturer" not in violated_fields
    assert "mfg_date" not in violated_fields
    assert "consumer_care" not in violated_fields

    # country_of_origin is not currently tracked.
    assert "country_of_origin" not in violated_fields


# ---------------------------------------------------------------------------
# Missing-field pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_missing_fields_label():
    """
    An empty OCR result should cause all five currently supported
    mandatory Field Mapping fields to be reported as missing.

    country_of_origin is intentionally excluded because it is not currently
    tracked by Field Mapping.
    """
    with patch(
        "app.api.v1.router.extract_text_from_image",
        new=AsyncMock(
            return_value=[]
        ),
    ):
        transport = ASGITransport(app=app)

        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            files = {
                "image": (
                    "blank.jpg",
                    _fake_image_bytes(),
                    "image/jpeg",
                )
            }

            response = await client.post(
                "/api/v1/scans/init",
                files=files,
            )

    assert response.status_code == 201, response.text

    data = response.json()

    assert data["is_compliant"] is False

    violated_fields = {
        violation["field_name"]
        for violation in data["violations"]
    }

    assert violated_fields == {
        "mrp",
        "net_quantity",
        "manufacturer",
        "mfg_date",
        "consumer_care",
    }


# ---------------------------------------------------------------------------
# OCR failure handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_ocr_failure_does_not_crash():
    """
    OCR failure should not bring down the API request.

    The current application behavior is expected to be made resilient
    to an OCR exception. This test documents that requirement.
    """
    with patch(
        "app.api.v1.router.extract_text_from_image",
        new=AsyncMock(
            side_effect=RuntimeError(
                "simulated OCR failure"
            )
        ),
    ):
        transport = ASGITransport(app=app)

        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            files = {
                "image": (
                    "broken.jpg",
                    _fake_image_bytes(),
                    "image/jpeg",
                )
            }

            response = await client.post(
                "/api/v1/scans/init",
                files=files,
            )

    assert response.status_code == 201, response.text

    data = response.json()

    assert "scan_id" in data
    assert data["status"] == "completed"
    assert data["is_compliant"] is False