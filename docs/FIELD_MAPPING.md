# Field Mapping Module

## Purpose

The Field Mapping module converts OCR output from product/package labels into five compliance-relevant structured fields:

- `MRP`
- `NET_QUANTITY`
- `MANUFACTURER_ADDRESS`
- `MANUFACTURING_DATE`
- `CONSUMER_CARE`

The implementation is deterministic first: OCR text is normalized, labels/anchors are detected, candidate values are extracted with bounded regex rules, and candidates are ranked using contextual and proximity signals. The mapper accepts either raw OCR text or OCR token dictionaries and can preserve token-level provenance such as bounding boxes, confidence, and language metadata.

## Mapping approach

### 1. Normalization

Text is lightly normalized before matching. The normalization handles whitespace, common OCR substitutions, MRP/quantity label variants, Devanagari digits, common OCR misspellings, and currency formatting while retaining the original OCR evidence separately.

### 2. Regex/keyword-first extraction

Each field has dedicated positive labels/anchors and negative-context protections. Candidate extraction is intentionally narrow so that unrelated numbers are not accepted simply because they look like a valid value.

For MRP, the mapper looks for explicit MRP/Maximum Retail Price context and currency/value patterns. Offer, discount, save, and similar price contexts are treated as negative signals.

For net quantity, the mapper uses packaging labels such as Net Quantity, Net Qty, Net Weight, Net Wt, Net Vol, Quantity, Weight, and Contents, together with supported unit variants.

For manufacturer address, the mapper uses manufacturer/manufactured-by/marketed-by style declarations and contextual ranking rather than treating every company/address on the label as the manufacturer.

For manufacturing date, the mapper recognizes manufacturing/packing labels and date formats such as numeric day/month/year forms and month-name/year forms. Expiry/use-by/best-before contexts are kept separate.

For consumer care, the mapper recognizes consumer/customer-care labels and extracts phone/email evidence without treating unrelated contact information as a consumer-care field.

### 3. Candidate scoring and bounded search

Candidate acceptance uses supporting label context, field-specific positive/negative evidence, and proximity. Search windows are bounded by label-like boundaries so one label cannot scan across another field and steal its value. This favors explainability and reduces false positives.

### 4. Structured output

The public mapping result contains one result object for each supported field. Results contain the extracted `value`, confidence, raw evidence, and candidate/provenance information where available.

Typical value shapes are:

```json
{
  "MRP": {
    "value": 301.0,
    "confidence": "high",
    "raw_evidence": "Rs.301.00"
  },
  "NET_QUANTITY": {
    "value": {
      "amount": 340.0,
      "unit": "ml"
    },
    "confidence": "high"
  },
  "MANUFACTURER_ADDRESS": {
    "value": "...",
    "confidence": "high"
  },
  "MANUFACTURING_DATE": {
    "value": "2025-12",
    "confidence": "high"
  },
  "CONSUMER_CARE": {
    "value": {
      "phone": "...",
      "email": "..."
    },
    "confidence": "high"
  }
}
