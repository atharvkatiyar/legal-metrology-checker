from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types


# ---------------------------------------------------------------------------
# Environment / configuration
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BACKEND_DIR / ".env")

MODEL_NAME = "gemini-3.6-flash"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class GeminiExtractionError(RuntimeError):
    """Raised when Gemini extraction fails or returns invalid data."""


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

def _get_client() -> genai.Client:
    """
    Create a Gemini API client from the local environment.

    The API key is never hardcoded and is never printed.
    """
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise GeminiExtractionError(
            "GEMINI_API_KEY is not configured"
        )

    return genai.Client(
        api_key=api_key
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_prompt() -> str:
    """
    Build the visual extraction prompt.

    The prompt deliberately focuses Gemini on extraction rather than
    legal/compliance decisions. Final validation remains in application code.
    """
    return """
You are the visual extraction engine for a Legal Metrology compliance checker.

Analyze the provided product/package image and extract these five fields:

1. MRP
2. NET_QUANTITY
3. MANUFACTURER_ADDRESS
4. MANUFACTURING_DATE
5. CONSUMER_CARE

GENERAL RULES:
- Read only information visibly present in the image.
- Never invent, infer, correct, or "fix" a value.
- If a field cannot be reliably identified, return null.
- Use the information actually printed on the package.
- Do not include unrelated nearby text or numbers.

MRP:
- Return only the actual Maximum Retail Price / MRP.
- Prefer a price explicitly associated with:
  "MRP", "M.R.P.", "Maximum Retail Price", or equivalent wording.
- Ignore offer prices, sale prices, discount prices, saving amounts,
  and unit prices such as "Rs. X per ml".
- If multiple prices are visible, choose only the price explicitly
  associated with MRP.
- Return only one MRP value.
- Preserve the printed price including currency when visible.

NET_QUANTITY:
- Return a value ONLY when the amount is explicitly associated with:
  "Net Qty", "Net Quantity", "Net Wt", "Net Weight",
  "Net Contents", "Contents", or equivalent packaging wording.
- Do NOT classify an arbitrary grams/ml measurement as net quantity.
- Ignore nutritional serving sizes, ingredient quantities,
  concentration values, pack components, and unrelated measurements.
- Return only the amount and unit.

MANUFACTURER_ADDRESS:
- Identify the actual manufacturer/manufactured-by declaration.
- Distinguish the manufacturer from:
  importer, marketer, distributor, seller, brand owner,
  and customer-care information.
- If several manufacturing facilities are explicitly listed
  under the same manufacturer declaration, include all relevant
  facilities.
- If one manufacturer is clearly identified, return that manufacturer's
  complete printed address.
- Do not select an unrelated company merely because its name appears
  elsewhere on the package.
- Do not summarize or shorten the address.

MANUFACTURING_DATE:
- Return the manufacturing or packing date.
- Do NOT return expiry, use-by, or best-before dates.
- Prefer dates explicitly associated with:
  MFG, MFD, Manufactured, Manufacturing Date,
  Packed On, Packing Date, or equivalent wording.
- Never change or correct a digit.
- Never infer a different year.
- Recognize formats including:
  DD/MM/YYYY
  MM/YYYY
  DD/MON/YY
  DD/MON/YYYY
  MON.YY
  MON YY
  MON YYYY
- A value such as "DEC.25" is valid when it is visibly associated
  with manufacturing or packing.
- Never use:
  EXP, Expiry, Use By, Best Before,
  or similar expiry wording.

CONSUMER_CARE:
- Extract the actual consumer/customer-care information.
- This may be:
  a phone number,
  an email address,
  a company/contact name,
  or a combination of these.
- Return the relevant consumer-care value itself.
- If the package explicitly identifies a company or contact person
  as the consumer-care contact, preserve that company/contact name.
- Do not include a long explanatory sentence when the actual contact
  value can be isolated.
- Do not substitute a general manufacturer/importer address unless
  it is explicitly presented as consumer-care information.

OUTPUT:
Return ONLY valid JSON.

Return exactly these five keys:

{
  "MRP": null,
  "NET_QUANTITY": null,
  "MANUFACTURER_ADDRESS": null,
  "MANUFACTURING_DATE": null,
  "CONSUMER_CARE": null
}

Do not add any other keys.
""".strip()


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_response(
    response_text: str,
) -> dict[str, Any]:
    """
    Parse Gemini's JSON response and validate the required fields.
    """
    text = response_text.strip()

    # Gemini may occasionally wrap JSON in a markdown code fence.
    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)

    except json.JSONDecodeError as exc:
        raise GeminiExtractionError(
            "Gemini returned invalid JSON"
        ) from exc

    if not isinstance(data, dict):
        raise GeminiExtractionError(
            "Gemini response must be a JSON object"
        )

    required_fields = {
        "MRP",
        "NET_QUANTITY",
        "MANUFACTURER_ADDRESS",
        "MANUFACTURING_DATE",
        "CONSUMER_CARE",
    }

    missing_fields = required_fields - data.keys()

    if missing_fields:
        raise GeminiExtractionError(
            "Gemini response is missing required fields: "
            + ", ".join(sorted(missing_fields))
        )

    return {
        field: data.get(field)
        for field in (
            "MRP",
            "NET_QUANTITY",
            "MANUFACTURER_ADDRESS",
            "MANUFACTURING_DATE",
            "CONSUMER_CARE",
        )
    }


# ---------------------------------------------------------------------------
# MIME type handling
# ---------------------------------------------------------------------------

def _get_mime_type(
    path: Path,
) -> str:
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
    }

    mime_type = mime_types.get(
        path.suffix.lower()
    )

    if mime_type is None:
        raise GeminiExtractionError(
            f"Unsupported image type: {path.suffix}"
        )

    return mime_type


# ---------------------------------------------------------------------------
# Public extraction function
# ---------------------------------------------------------------------------

def extract_fields_from_image(
    image_path: str | Path,
) -> dict[str, Any]:
    """
    Extract legal-metrology fields from one product image.

    Returns:
        {
            "MRP": ...,
            "NET_QUANTITY": ...,
            "MANUFACTURER_ADDRESS": ...,
            "MANUFACTURING_DATE": ...,
            "CONSUMER_CARE": ...
        }

    This service is intentionally isolated from the existing OCR and
    deterministic Field Mapping pipeline so Gemini can be evaluated
    independently before integration.
    """
    path = Path(image_path)

    if not path.is_file():
        raise FileNotFoundError(
            f"Image not found: {path}"
        )

    mime_type = _get_mime_type(path)

    client = _get_client()

    try:
        with open(
            path,
            "rb",
        ) as image_file:
            image_bytes = image_file.read()

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=mime_type,
                ),
                _build_prompt(),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

    except Exception as exc:
        raise GeminiExtractionError(
            f"Gemini request failed: {exc}"
        ) from exc

    response_text = getattr(
        response,
        "text",
        None,
    )

    if not response_text:
        raise GeminiExtractionError(
            "Gemini returned an empty response"
        )

    return _parse_response(
        response_text
    )


__all__ = [
    "GeminiExtractionError",
    "extract_fields_from_image",
]