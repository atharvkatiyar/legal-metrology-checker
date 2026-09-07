# Field Mapping — Demo Notes

## What this module does

The Field Mapping module converts OCR output from a product/package label into five structured Legal Metrology fields:

- MRP
- NET_QUANTITY
- MANUFACTURER_ADDRESS
- MANUFACTURING_DATE
- CONSUMER_CARE

The module is designed for explainability and safe extraction rather than blindly accepting every number or text fragment returned by OCR.

## Extraction pipeline

The mapping flow is:

OCR output
→ text normalization
→ label/keyword detection
→ candidate extraction
→ contextual/proximity scoring
→ structured field result
→ LLM fallback only when required

### Regex/keyword-first

Deterministic rules are tried first because they are:

- fast
- explainable
- reproducible
- inexpensive
- less likely to hallucinate

The mapper uses field-specific labels, negative-context protection, bounded search windows, and candidate ranking.

Examples:

- MRP: MRP / Maximum Retail Price and currency-value patterns
- Net quantity: Net Qty / Net Weight / Net Vol / Contents and unit patterns
- Manufacturing date: MFD / MFG / Manufactured / Packed On and supported date formats
- Manufacturer: manufacturer/manufactured-by declarations plus contextual selection
- Consumer care: consumer/customer-care labels, phone numbers, and email addresses

## Why not use the LLM for everything?

Using the LLM for every field would add unnecessary latency, API cost, and dependency on an external service.

The deterministic mapper handles straightforward cases locally. The LLM is used only when deterministic extraction leaves fields missing or low-confidence.

This gives a hybrid design:

Deterministic extraction first
→ LLM only when necessary

## LLM fallback

The fallback receives the product image and asks the vision model to extract the five supported fields.

Important safeguards:

- API key is read from `GEMINI_API_KEY`
- credentials are not hardcoded
- the fallback has a 60-second timeout
- malformed/error responses do not crash the scan
- deterministic results are preserved when Gemini fails or times out
- the model is instructed to return `null` when information is not reliably visible
- the LLM performs extraction only; compliance decisions remain in application code

## Output structure

Each supported field is returned as a structured result containing the extracted value and confidence information.

The result can also retain diagnostic/provenance information such as:

- raw OCR evidence
- extraction method
- candidate information
- bounding-box information
- confidence
- ambiguity indicators

### Typical value shapes

MRP:

{
  "value": 301.0,
  "confidence": "high"
}

NET_QUANTITY:

{
  "value": {
    "amount": 340.0,
    "unit": "ml"
  },
  "confidence": "high"
}

MANUFACTURING_DATE:

{
  "value": "2025-12",
  "confidence": "high"
}

CONSUMER_CARE:

{
  "value": {
    "phone": "1-800-208-1930",
    "email": "contactus@himalayawellness.com"
  },
  "confidence": "high"
}

## Important design decision: conservative extraction

A wrong value can be more harmful than a missing value in a compliance workflow.

Therefore, the mapper is intentionally conservative.

It prefers:

"No reliable candidate found"

over:

"Return an unrelated number because it looks valid."

## Known limitations

### OCR dependency

Field Mapping cannot reliably recover information that OCR completely misses or corrupts.

Examples include:

- missing digits
- merged characters
- distorted dates
- OCR substitutions
- text split across unusual token boundaries

### Multiple addresses

A product package may contain:

- manufacturer
- marketer
- importer
- distributor
- multiple manufacturing facilities

Selecting the correct manufacturer address therefore requires context and ranking rather than simply extracting the first address-shaped string.

### Dates

Manufacturing/packing dates can appear in many formats, while expiry/use-by/best-before information may be nearby.

The mapper therefore separates manufacturing-date context from expiry context.

### Consumer care

Phone numbers and emails may be split across multiple OCR tokens or lines, which can make complete reconstruction difficult.

## Testing strategy

Two levels of validation are used:

### 1. Deterministic regression tests

These cover normal, noisy, and adversarial field-mapping cases without requiring OCR.

### 2. End-to-end labeled dataset

The labeled batch contains 39 real product/package images.

This evaluates:

OCR
+
Field Mapping
+
real-world label variation

The labeled batch is therefore an end-to-end accuracy check, not just a unit-test check.

## Current verification

Before the final labeled-batch run:

- Field Mapping fallback tests: 6/6 passed
- Pipeline integration tests: 3/3 passed
- Integration warning observed: Python `crypt` deprecation from Passlib; unrelated to Field Mapping

The final 39-image accuracy result should be reported only after that run completes.

## Demo explanation

### 30-second explanation

"My module takes OCR output and maps it into five legal-metrology fields: MRP, net quantity, manufacturer address, manufacturing date, and consumer care. We use a regex and keyword-first approach with normalization and contextual scoring so unrelated numbers are not accidentally selected. The output is structured JSON with confidence and evidence. When deterministic extraction cannot confidently find a field, we use an LLM fallback on the product image. The fallback has timeout and error handling, so the system can safely fall back to deterministic extraction if the external model is unavailable."

### Why regex first?

"Because these fields are highly structured. Deterministic rules are faster, cheaper, explainable, and easier to validate. The LLM is reserved for ambiguous or missing cases."

### What happens when OCR is bad?

"We normalize common OCR errors and support multiple label, unit, currency, and date variants. But if OCR completely misses information, Field Mapping cannot reliably reconstruct information that was never captured."

### What happens if Gemini fails?

"The scan does not fail. Timeout and error handling preserve the deterministic mapping result."

### What is the biggest limitation?

"Upstream OCR quality is the biggest limitation, especially when small-print text or batch/date information is distorted or missing."

## Demo advice

Do not claim 100% end-to-end accuracy.

Separate these three things when discussing results:

1. deterministic Field Mapping correctness
2. LLM fallback correctness
3. end-to-end OCR + Field Mapping accuracy

The third number is the one measured by the 39-image labeled batch.
