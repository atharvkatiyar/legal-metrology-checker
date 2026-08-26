"""
Automated tests — Field Mapping Aug 26 deliverable (MRP + Net Quantity, English)
Run: python3 test_field_mapping.py
"""

import sys
from field_mapping import resolve_mrp, resolve_net_quantity, map_fields

PASS = 0
FAIL = 0
FAILURES = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {detail}")
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail and not condition else ""))


# ===========================================================================
# MRP — normal cases
# ===========================================================================

r = resolve_mrp("MRP ₹249")
check("MRP basic ₹ symbol", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP: ₹249")
check("MRP with colon", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("M.R.P. ₹1,249")
check("MRP with comma thousands", r.value == 1249.0, f"got {r.value}")

r = resolve_mrp("MRP Rs. 249/-")
check("MRP Rs. with trailing /-", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP INR 249")
check("MRP INR", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("Maximum Retail Price ₹249")
check("MRP full label 'Maximum Retail Price'", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP Rs 249")
check("MRP Rs no period", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("M.R.P ₹249")
check("MRP no trailing period", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP ₹249.50")
check("MRP decimal value", r.value == 249.50, f"got {r.value}")

# ===========================================================================
# MRP — punctuation / spacing variation (OCR noise)
# ===========================================================================

r = resolve_mrp("M R P ₹249")
check("MRP spaced letters (OCR noise)", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP:₹249")
check("MRP no space before currency", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP    ₹   249")
check("MRP excess whitespace", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("mrp ₹249")
check("MRP lowercase", r.value == 249.0, f"got {r.value}")

# ===========================================================================
# MRP — multiple candidates / conflicting context
# ===========================================================================

r = resolve_mrp("Offer Price ₹199 MRP ₹249")
check("MRP correctly picked over preceding Offer Price", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP ₹249 Offer Price ₹199")
check("Offer Price after MRP does not override", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("Special Price ₹150 M.R.P. ₹249 Save ₹99")
check("MRP picked among multiple conflicting labels", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP ₹249 MRP ₹299")
check("Multiple MRP candidates -> deterministic nearest/first pick", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP ₹249 Maximum Retail Price ₹299")
check("MRP label claims its own nearest value, not the later label's", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("Maximum Retail Price ₹299 MRP ₹249")
check("First label (Maximum Retail Price) claims its own nearest value", r.value == 299.0, f"got {r.value}")

# ===========================================================================
# MRP — negative / false positive prevention
# ===========================================================================

r = resolve_mrp("Offer Price ₹199")
check("Offer Price alone -> no MRP", r.value is None, f"got {r.value}")

r = resolve_mrp("Save ₹50 today")
check("Save amount alone -> no MRP", r.value is None, f"got {r.value}")

r = resolve_mrp("You Pay ₹199 only")
check("You Pay alone -> no MRP", r.value is None, f"got {r.value}")

r = resolve_mrp("₹249 great value")
check("Bare currency with no MRP label -> no MRP", r.value is None, f"got {r.value}")

r = resolve_mrp("Discount ₹249")
check("Discount label near amount -> suppressed", r.value is None, f"got {r.value}")

# ===========================================================================
# MRP — malformed OCR / malformed currency symbol
# ===========================================================================

r = resolve_mrp("MRP ?249")
check("Malformed '?' currency accepted only with MRP label", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("?249 random text")
check("Malformed '?' currency with NO MRP label -> rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRPRs249")
check("Merged label+currency+amount (heavy OCR noise)", r.value in (None, 249.0), f"got {r.value}")

# ===========================================================================
# MRP — without currency symbol (must require strong adjacent MRP context)
# ===========================================================================

r = resolve_mrp("MRP 249")
check("MRP amount-only, no currency", r.value == 249.0, f"got {r.value}")
check("MRP amount-only -> confidence forced low", r.confidence == "low", f"got {r.confidence}")

r = resolve_mrp("MRP: 249")
check("MRP: amount-only with colon", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("249")
check("Bare number, no label at all -> rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("Price 249")
check("'Price' alone is not a recognized MRP label -> rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("₹249")
check("Bare currency, no MRP label -> rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP ₹249")
check("Currency match confidence outranks amount-only", r.confidence == "high", f"got {r.confidence}")

# ===========================================================================
# MRP — sanity checks (zero / malformed)
# ===========================================================================

r = resolve_mrp("MRP ₹0")
check("MRP zero value rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP 0")
check("MRP amount-only zero rejected", r.value is None, f"got {r.value}")

# ===========================================================================
# MRP — numeric boundary / no-truncation regression (Aug26c hardening)
# ===========================================================================

r = resolve_mrp("MRP ₹2490")
check("MRP 4-digit no-comma amount not truncated to 249", r.value == 2490.0, f"got {r.value}")

r = resolve_mrp("MRP ₹249.99")
check("MRP 2-decimal-digit amount valid", r.value == 249.99, f"got {r.value}")

r = resolve_mrp("MRP ₹249.999")
check("MRP 3-decimal-digit amount rejected outright (not truncated to 249.99)",
      r.value is None, f"got {r.value}")

r = resolve_mrp("MRP ₹1,249")
check("MRP comma-grouped thousands still valid", r.value == 1249.0, f"got {r.value}")

r = resolve_mrp("MRP ₹12,34,567")
check("MRP Indian-style multi-comma grouping supported", r.value == 1234567.0, f"got {r.value}")

r = resolve_mrp("MRP ₹1,234,567")
check("MRP international-style all-3-digit grouping supported", r.value == 1234567.0, f"got {r.value}")

r = resolve_mrp("MRP ₹1,23,456")
check("MRP Indian lakh-style grouping supported", r.value == 123456.0, f"got {r.value}")

r = resolve_mrp("MRP ₹1,23,45")
check("MRP malformed comma grouping rejected outright, not truncated to 12345",
      r.value is None, f"got {r.value}")

r = resolve_mrp("MRP ₹123,45")
check("MRP malformed 2-digit trailing group rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP ₹12,3456")
check("MRP malformed 4-digit trailing group rejected", r.value is None, f"got {r.value}")

# ===========================================================================
# MRP — strict comma grouping, final pass (Aug26f)
# ===========================================================================

r = resolve_mrp("MRP 123")
check("MRP plain 3-digit no-comma valid", r.value == 123.0, f"got {r.value}")

r = resolve_mrp("MRP 1234")
check("MRP plain 4-digit no-comma valid", r.value == 1234.0, f"got {r.value}")

r = resolve_mrp("MRP 12345")
check("MRP plain 5-digit no-comma valid", r.value == 12345.0, f"got {r.value}")

r = resolve_mrp("MRP ₹1,234")
check("MRP international 1,234 valid", r.value == 1234.0, f"got {r.value}")

r = resolve_mrp("MRP ₹12,345")
check("MRP international 12,345 valid", r.value == 12345.0, f"got {r.value}")

r = resolve_mrp("MRP ₹123,456")
check("MRP international 123,456 valid", r.value == 123456.0, f"got {r.value}")

r = resolve_mrp("MRP ₹1,00,000")
check("MRP Indian 1,00,000 (leading zero group) valid", r.value == 100000.0, f"got {r.value}")

r = resolve_mrp("MRP ₹1234,567")
check("MRP malformed 4-digit leading group rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP ₹1,2,345")
check("MRP malformed 1-digit middle group rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP ₹1,234,56")
check("MRP malformed 2-digit trailing group after valid international group rejected",
      r.value is None, f"got {r.value}")

r = resolve_mrp("MRP 249")
check("MRP amount-only fallback still valid", r.value == 249.0, f"got {r.value}")
check("MRP amount-only fallback still low-confidence", r.confidence == "low", f"got {r.confidence}")

r = resolve_mrp("MRP 2499")
check("MRP amount-only 4-digit not truncated to 249", r.value == 2499.0, f"got {r.value}")

# ===========================================================================
# MRP — amount-only false-positive prevention (Aug26d hardening)
# ===========================================================================

r = resolve_mrp("MRP abc249")
check("MRP amount-only rejects number glued to leading letters", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP xyz249")
check("MRP amount-only rejects number glued to different leading letters", r.value is None, f"got {r.value}")

r = resolve_mrp("MRPabc249")
check("MRP amount-only rejects fully glued alphanumeric attachment", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP: 249")
check("MRP amount-only still valid with colon", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP    249")
check("MRP amount-only still valid with excess whitespace", r.value == 249.0, f"got {r.value}")

# ===========================================================================
# MRP — currency token boundary (Aug26e hardening)
# ===========================================================================

r = resolve_mrp("MRP ₹249abc")
check("MRP currency value glued to trailing letters rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP Rs 249abc")
check("MRP Rs value glued to trailing letters rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP INR 249abc")
check("MRP INR value glued to trailing letters rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP ₹249.00abc")
check("MRP decimal value glued to trailing letters rejected outright, not truncated to 249",
      r.value is None, f"got {r.value}")

r = resolve_mrp("MRP ₹249")
check("MRP currency value with no trailing junk still valid (regression)", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP Rs. 249")
check("MRP Rs. value with no trailing junk still valid (regression)", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP ₹249/-")
check("MRP value with trailing '/-' still valid (regression)", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP ₹249)")
check("MRP value followed by ')' still valid (punctuation, not letters)", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP ₹249.")
check("MRP value followed by trailing '.' still valid (punctuation, not letters)", r.value == 249.0, f"got {r.value}")

r = resolve_mrp("MRP ₹249|next")
check("MRP value followed by '|' still valid (punctuation, not letters)", r.value == 249.0, f"got {r.value}")

# ===========================================================================
# MRP — negative / signed value rejection (Aug26c hardening)
# ===========================================================================

r = resolve_mrp("MRP -₹249")
check("MRP with sign before currency symbol rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP ₹-249")
check("MRP with sign after currency symbol rejected (no fallthrough to amount-only)",
      r.value is None, f"got {r.value}")

r = resolve_mrp("MRP -249")
check("MRP amount-only with leading minus rejected", r.value is None, f"got {r.value}")

r = resolve_mrp("MRP +249")
check("MRP amount-only with leading plus rejected (documented: signs always rejected)",
      r.value is None, f"got {r.value}")

r = resolve_mrp("MRP +₹249")
check("MRP with plus sign before currency symbol rejected", r.value is None, f"got {r.value}")

# ===========================================================================
# MRP — empty / missing input
# ===========================================================================

r = resolve_mrp("")
check("Empty string input -> no MRP, no crash", r.value is None, f"got {r.value}")

r = resolve_mrp(None or "")
check("None-coerced empty input -> no MRP", r.value is None, f"got {r.value}")

r = resolve_mrp("Net Qty 500 g")
check("No MRP context at all -> no MRP", r.value is None, f"got {r.value}")


# ===========================================================================
# Net Quantity — normal cases
# ===========================================================================

r = resolve_net_quantity("Net Qty. 500 g")
check("Net Qty basic", r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Quantity: 1 kg")
check("Net Quantity with colon", r.value == {"amount": 1.0, "unit": "kg"}, f"got {r.value}")

r = resolve_net_quantity("Net Wt. 250 ml")
check("Net Wt ml", r.value == {"amount": 250.0, "unit": "ml"}, f"got {r.value}")

r = resolve_net_quantity("Net Weight 1.5 L")
check("Net Weight decimal litres", r.value == {"amount": 1.5, "unit": "l"}, f"got {r.value}")

# ===========================================================================
# Net Quantity — spacing / casing variation
# ===========================================================================

r = resolve_net_quantity("Net Qty 500g")
check("Net Qty missing space before unit", r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("NET QTY 500 G")
check("Net Qty all caps", r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("net qty 500 ML")
check("Net Qty lowercase label, uppercase unit", r.value == {"amount": 500.0, "unit": "ml"}, f"got {r.value}")

r = resolve_net_quantity("Net   Qty.    500   g")
check("Net Qty excess whitespace", r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

# ===========================================================================
# Net Quantity — negative / conflict cases
# ===========================================================================

r = resolve_net_quantity("Protein 20 g")
check("Protein alone -> no Net Quantity", r.value is None, f"got {r.value}")

r = resolve_net_quantity("Serving Size 50 g")
check("Serving Size alone -> no Net Quantity", r.value is None, f"got {r.value}")

r = resolve_net_quantity("Energy 250 kcal")
check("Energy value -> no Net Quantity (no unit match anyway)", r.value is None, f"got {r.value}")

r = resolve_net_quantity("Pack of 6")
check("Pack of N -> no Net Quantity", r.value is None, f"got {r.value}")

# Serving size / nutrition value physically near a real Net Qty label
r = resolve_net_quantity("Serving Size 50 g Net Qty. 500 g")
check("Net Qty correct despite nearby Serving Size", r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty. 500 g Protein 20 g Fat 5 g")
check("Net Qty correct despite following nutrition values", r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

# ===========================================================================
# Net Quantity — multiple candidates
# ===========================================================================

r = resolve_net_quantity("Net Qty 500 g Net Wt 500 g")
check("Multiple consistent Net Qty labels -> resolves", r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 500 g Net Qty 1 kg")
check("Multiple differing Net Qty candidates -> deterministic nearest/first pick",
      r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

# ===========================================================================
# Net Quantity — expanded English unit variants
# ===========================================================================

r = resolve_net_quantity("Net Qty 500 grams")
check("Net Qty 'grams' -> normalized to 'g'", r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Wt. 1 gram")
check("Net Wt 'gram' (singular) -> normalized to 'g'", r.value == {"amount": 1.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Weight 2 kilograms")
check("Net Weight 'kilograms' -> normalized to 'kg'", r.value == {"amount": 2.0, "unit": "kg"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 1 kilogram")
check("Net Qty 'kilogram' (singular) -> normalized to 'kg'", r.value == {"amount": 1.0, "unit": "kg"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 500 milligrams")
check("Net Qty 'milligrams' -> normalized to 'mg'", r.value == {"amount": 500.0, "unit": "mg"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 250 millilitres")
check("Net Qty 'millilitres' -> normalized to 'ml'", r.value == {"amount": 250.0, "unit": "ml"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 250 milliliters")
check("Net Qty 'milliliters' (US spelling) -> normalized to 'ml'", r.value == {"amount": 250.0, "unit": "ml"}, f"got {r.value}")

r = resolve_net_quantity("Net Weight 1 litre")
check("Net Weight 'litre' -> normalized to 'l'", r.value == {"amount": 1.0, "unit": "l"}, f"got {r.value}")

r = resolve_net_quantity("Net Weight 1 liter")
check("Net Weight 'liter' (US spelling) -> normalized to 'l'", r.value == {"amount": 1.0, "unit": "l"}, f"got {r.value}")

# ===========================================================================
# Net Quantity — comma-grouped values (Aug26d hardening)
# ===========================================================================

r = resolve_net_quantity("Net Qty 1,500 g")
check("Net Qty comma-grouped '1,500 g' -> full 1500, not truncated to 500",
      r.value == {"amount": 1500.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 12,500 g")
check("Net Qty comma-grouped '12,500 g' -> 12500",
      r.value == {"amount": 12500.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Weight 1,500 ml")
check("Net Weight comma-grouped '1,500 ml' -> 1500",
      r.value == {"amount": 1500.0, "unit": "ml"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 1,23,456 g")
check("Net Qty Indian lakh-style grouping supported",
      r.value == {"amount": 123456.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 12,34,567 g")
check("Net Qty Indian-style multi-comma grouping supported",
      r.value == {"amount": 1234567.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 1,234,567 g")
check("Net Qty international-style all-3-digit grouping supported",
      r.value == {"amount": 1234567.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 1,23,45 g")
check("Net Qty malformed comma grouping rejected outright, not truncated to 12345",
      r.value is None, f"got {r.value}")

r = resolve_net_quantity("Net Qty 123,45 g")
check("Net Qty malformed 2-digit trailing group rejected", r.value is None, f"got {r.value}")

r = resolve_net_quantity("Net Qty 12,3456 g")
check("Net Qty malformed 4-digit trailing group rejected", r.value is None, f"got {r.value}")

r = resolve_net_quantity("Net Qty 1234,567 g")
check("Net Qty malformed 4-digit leading group rejected", r.value is None, f"got {r.value}")

r = resolve_net_quantity("Net Qty 1,00,000 g")
check("Net Qty Indian 1,00,000 (leading zero group) valid",
      r.value == {"amount": 100000.0, "unit": "g"}, f"got {r.value}")

# ===========================================================================
# Net Quantity — sanity checks (zero / malformed)
# ===========================================================================

r = resolve_net_quantity("Net Qty 0 g")
check("Net Qty zero value rejected", r.value is None, f"got {r.value}")

# ===========================================================================
# Net Quantity — negative / signed value rejection (Aug26c hardening)
# ===========================================================================

r = resolve_net_quantity("Net Qty -5 g")
check("Net Qty with leading minus rejected", r.value is None, f"got {r.value}")

r = resolve_net_quantity("Net Weight -1 kg")
check("Net Weight with leading minus rejected", r.value is None, f"got {r.value}")

r = resolve_net_quantity("Net Qty +5 g")
check("Net Qty with leading plus rejected (documented: signs always rejected, consistent with MRP policy)",
      r.value is None, f"got {r.value}")

r = resolve_net_quantity("Net Qty 500 g")
check("Net Qty unsigned value still valid (regression)", r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

r = resolve_net_quantity("Net Qty 1.5 kg")
check("Net Qty decimal value still valid (regression)", r.value == {"amount": 1.5, "unit": "kg"}, f"got {r.value}")

# ===========================================================================
# Net Quantity — malformed OCR / empty input
# ===========================================================================

r = resolve_net_quantity("NetQty500g")
check("Fully merged OCR text (heavy noise)", r.value in (None, {"amount": 500.0, "unit": "g"}), f"got {r.value}")

r = resolve_net_quantity("")
check("Empty string input -> no Net Quantity, no crash", r.value is None, f"got {r.value}")

r = resolve_net_quantity("MRP ₹249")
check("No Net Qty context at all -> no Net Quantity", r.value is None, f"got {r.value}")

# ===========================================================================
# Integration-level: map_fields() combined + list-of-tokens input
# ===========================================================================

result = map_fields("MRP ₹249 Net Qty. 500 g")
check("map_fields combined string input - MRP", result["MRP"]["value"] == 249.0, f"got {result['MRP']}")
check("map_fields combined string input - NetQty",
      result["NET_QUANTITY"]["value"] == {"amount": 500.0, "unit": "g"}, f"got {result['NET_QUANTITY']}")

token_input = [{"text": "MRP"}, {"text": "₹249"}, {"text": "Net"}, {"text": "Qty."}, {"text": "500"}, {"text": "g"}]
result2 = map_fields(token_input)
check("map_fields token-list input works (forward-compat OCR schema)",
      result2["MRP"]["value"] == 249.0, f"got {result2['MRP']}")

result3 = map_fields([])
check("map_fields empty token list -> no crash, no fields",
      result3["MRP"]["value"] is None and result3["NET_QUANTITY"]["value"] is None)

result4 = map_fields(None)
check("map_fields None input -> no crash",
      result4["MRP"]["value"] is None and result4["NET_QUANTITY"]["value"] is None)


print("\n" + "=" * 60)
print(f"TOTAL: {PASS + FAIL}   PASSED: {PASS}   FAILED: {FAIL}")
if FAILURES:
    print("\nFailed tests:")
    for f in FAILURES:
        print(f"  - {f}")
print("=" * 60)

sys.exit(0 if FAIL == 0 else 1)
