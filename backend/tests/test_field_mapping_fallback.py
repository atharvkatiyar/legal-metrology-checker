from unittest.mock import AsyncMock, patch

import pytest

from app.services.field_mapping_fallback import (
    GEMINI_TIMEOUT_SECONDS,
    map_fields_with_fallback,
)


@pytest.mark.asyncio
async def test_regex_result_is_kept_without_gemini():
    tokens = [
        {"text": "MRP Rs. 100"},
        {"text": "Net Qty 500 g"},
        {
            "text": (
                "Manufactured by ABC Company, Jaipur"
            )
        },
        {
            "text": "Mfg Date 01/01/2026"
        },
        {
            "text": "Customer Care 1800-123-4567"
        },
    ]

    with patch(
        "app.services.field_mapping_fallback._call_gemini_with_timeout",
        new=AsyncMock(),
    ) as mock_gemini:
        result = await map_fields_with_fallback(
            tokens,
            image_path="fake.jpg",
        )

    assert result["MRP"]["value"] == 100.0
    assert (
        result["NET_QUANTITY"]["value"]["amount"]
        == 500.0
    )

    mock_gemini.assert_not_called()


@pytest.mark.asyncio
async def test_gemini_fills_missing_fields():
    tokens = [
        {"text": "MRP Rs. 100"},
        {"text": "Net Qty 500 g"},
    ]

    gemini_result = {
        "MRP": "Rs. 100",
        "NET_QUANTITY": "500 g",
        "MANUFACTURER_ADDRESS": (
            "ABC Company, Jaipur, Rajasthan"
        ),
        "MANUFACTURING_DATE": "01/01/2026",
        "CONSUMER_CARE": "1800-123-4567",
    }

    with patch(
        "app.services.field_mapping_fallback._call_gemini_with_timeout",
        new=AsyncMock(
            return_value=gemini_result
        ),
    ) as mock_gemini:
        result = await map_fields_with_fallback(
            tokens,
            image_path="fake.jpg",
        )

    assert (
        result["MANUFACTURER_ADDRESS"]["value"]
        == "ABC Company, Jaipur, Rajasthan"
    )

    assert (
        result["MANUFACTURING_DATE"]["value"]
        == "01/01/2026"
    )

    assert (
        result["CONSUMER_CARE"]["value"]
        == "1800-123-4567"
    )

    assert result["MRP"]["value"] == 100.0

    assert (
        result["NET_QUANTITY"]["value"]["amount"]
        == 500.0
    )

    mock_gemini.assert_called_once_with(
        "fake.jpg"
    )


@pytest.mark.asyncio
async def test_gemini_failure_keeps_regex_result():
    tokens = [
        {"text": "MRP Rs. 100"},
        {"text": "Net Qty 500 g"},
    ]

    with patch(
        "app.services.field_mapping_fallback._call_gemini_with_timeout",
        new=AsyncMock(
            side_effect=Exception(
                "Gemini unavailable"
            )
        ),
    ):
        result = await map_fields_with_fallback(
            tokens,
            image_path="fake.jpg",
        )

    assert result["MRP"]["value"] == 100.0

    assert (
        result["NET_QUANTITY"]["value"]["amount"]
        == 500.0
    )

    assert (
        result["MANUFACTURER_ADDRESS"]["value"]
        is None
    )


@pytest.mark.asyncio
async def test_gemini_timeout_keeps_regex_result():
    tokens = [
        {"text": "MRP Rs. 100"},
        {"text": "Net Qty 500 g"},
    ]

    with patch(
        "app.services.field_mapping_fallback._call_gemini_with_timeout",
        new=AsyncMock(
            side_effect=TimeoutError()
        ),
    ):
        result = await map_fields_with_fallback(
            tokens,
            image_path="fake.jpg",
        )

    assert result["MRP"]["value"] == 100.0

    assert (
        result["NET_QUANTITY"]["value"]["amount"]
        == 500.0
    )