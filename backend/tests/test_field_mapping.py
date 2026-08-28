mport sys
from pathlib import Path

# Add the backend directory to Python's import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.field_mapping import (
    resolve_mrp, resolve_net_quantity, map_fields,
    resolve_manufacturer, resolve_manufacturing_date, resolve_consumer_care,
    FIELD_KEYWORDS,
)

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

r = resolve_mrp("MRP ₹123,45,678")
check(
    "MRP Indian-style 3-digit leading group supported",
    r.value == 12345678.0,
    f"got {r.value}"
)

r = resolve_mrp("MRP ₹1,23,45,678")
check(
    "MRP Indian-style long grouping supported",
    r.value == 12345678.0,
    f"got {r.value}"
)

r = resolve_mrp("MRP ₹1,23,45")
check(
    "MRP malformed comma grouping rejected outright, not truncated to 12345",
    r.value is None,
    f"got {r.value}"
)

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

r = resolve_mrp(None)
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

r = resolve_net_quantity("Net Qty 123,45,678 g")
check(
    "Net Qty Indian-style 3-digit leading group supported",
    r.value == {"amount": 12345678.0, "unit": "g"},
    f"got {r.value}"
)

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


# ===========================================================================
# AUG 27 — Manufacturer / Manufacturer Address
# ===========================================================================

r = resolve_manufacturer("Manufactured by ABC Foods Pvt Ltd, Pune, Maharashtra")
check("Manufacturer: 'Manufactured by' label", r.value == "ABC Foods Pvt Ltd, Pune, Maharashtra", f"got {r.value}")

r = resolve_manufacturer("Mfd. by XYZ Industries Ltd.")
check("Manufacturer: 'Mfd. by' punctuation variant", r.value == "XYZ Industries Ltd.", f"got {r.value}")

r = resolve_manufacturer("Mfg by XYZ Industries Ltd")
check("Manufacturer: 'Mfg by' (no period) variant", r.value == "XYZ Industries Ltd", f"got {r.value}")

r = resolve_manufacturer("Marketed by Fresh Foods India")
check("Manufacturer: 'Marketed by' label", r.value == "Fresh Foods India", f"got {r.value}")

r = resolve_manufacturer("Manufactured for Retail Chain Co.")
check("Manufacturer: 'Manufactured for' label", r.value == "Retail Chain Co.", f"got {r.value}")

r = resolve_manufacturer("Manufactured & Marketed by Sunrise Snacks Pvt Ltd, Delhi")
check("Manufacturer: 'Manufactured & Marketed by' compound label",
      r.value == "Sunrise Snacks Pvt Ltd, Delhi", f"got {r.value}")

r = resolve_manufacturer("Manufactured and Marketed by Sunrise Snacks Pvt Ltd, Delhi")
check("Manufacturer: 'Manufactured and Marketed by' spelled-out variant",
      r.value == "Sunrise Snacks Pvt Ltd, Delhi", f"got {r.value}")

r = resolve_manufacturer("Manufacturer: Global Foods Inc")
check("Manufacturer: bare 'Manufacturer' label with colon", r.value == "Global Foods Inc", f"got {r.value}")

r = resolve_manufacturer("Manufactured by ABC Foods Pvt Ltd, Pune MRP ₹249")
check("Manufacturer: boundary stops before following MRP field",
      r.value == "ABC Foods Pvt Ltd, Pune", f"got {r.value}")

r = resolve_manufacturer("Net Qty 500 g Manufactured by ABC Foods MRP ₹249")
check("Manufacturer: address extracted mid-string, bounded on both sides",
      r.value == "ABC Foods", f"got {r.value}")

r = resolve_manufacturer("MRP ₹249 Net Qty 500 g")
check("Manufacturer: missing label -> no false positive", r.value is None, f"got {r.value}")

r = resolve_manufacturer("Manufactured by ,")
check("Manufacturer: label with no real value after it -> rejected", r.value is None, f"got {r.value}")

r = resolve_manufacturer("Manufactured by 12345")
check("Manufacturer: digits-only value (no letters) rejected as implausible",
      r.value is None, f"got {r.value}")

r = resolve_manufacturer("")
check("Manufacturer: empty input -> no crash", r.value is None, f"got {r.value}")


# ===========================================================================
# AUG 27 — Manufacturing Date
# ===========================================================================

r = resolve_manufacturing_date("Mfg Date 01/06/2026")
check("Mfg Date: DD/MM/YYYY", r.value == "2026-06-01", f"got {r.value}")

r = resolve_manufacturing_date("Mfd Date: 15-03-2025")
check("Mfg Date: DD-MM-YYYY with colon punctuation", r.value == "2025-03-15", f"got {r.value}")

r = resolve_manufacturing_date("Manufacturing Date 2026.06.01")
check("Mfg Date: YYYY.MM.DD dot format", r.value == "2026-06-01", f"got {r.value}")

r = resolve_manufacturing_date("Manufactured Date 2026-06-01")
check("Mfg Date: YYYY-MM-DD dash format", r.value == "2026-06-01", f"got {r.value}")

r = resolve_manufacturing_date("Date of Manufacture 01.06.2026")
check("Mfg Date: 'Date of Manufacture' label, DD.MM.YYYY", r.value == "2026-06-01", f"got {r.value}")

r = resolve_manufacturing_date("Packed On 01/06/2026")
check("Mfg Date: 'Packed On' label", r.value == "2026-06-01", f"got {r.value}")

r = resolve_manufacturing_date("Packing Date 01/06/2026")
check("Mfg Date: 'Packing Date' label", r.value == "2026-06-01", f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 15 Jun 2026")
check("Mfg Date: month-name format", r.value == "2026-06-15", f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 01/06/26")
check("Mfg Date: 2-digit year still resolves", r.value == "2026-06-01", f"got {r.value}")
check("Mfg Date: 2-digit year is lower confidence than 4-digit year",
      r.confidence == "low", f"got {r.confidence}")

r = resolve_manufacturing_date("Mfg Date 01/06/2026")
check("Mfg Date: 4-digit year is high confidence", r.confidence == "high", f"got {r.confidence}")

r = resolve_manufacturing_date("Mfg Date 01/06/2026 Expiry Date 01/06/2027")
check("Mfg Date: does not confuse with a following Expiry Date",
      r.value == "2026-06-01", f"got {r.value}")

r = resolve_manufacturing_date("Expiry Date 01/06/2027")
check("Mfg Date: expiry-only text -> no false manufacturing date", r.value is None, f"got {r.value}")

r = resolve_manufacturing_date("Best Before 01/06/2027")
check("Mfg Date: 'Best Before' alone -> no false manufacturing date", r.value is None, f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 45/13/2026")
check("Mfg Date: malformed day/month rejected outright", r.value is None, f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 99/99/9999")
check("Mfg Date: wildly malformed date rejected", r.value is None, f"got {r.value}")

r = resolve_manufacturing_date("Net Qty 500 g")
check("Mfg Date: missing label -> no false positive", r.value is None, f"got {r.value}")

r = resolve_manufacturing_date("")
check("Mfg Date: empty input -> no crash", r.value is None, f"got {r.value}")


# ===========================================================================
# AUG 27 — Consumer Care
# ===========================================================================

r = resolve_consumer_care("Consumer Care 1800-123-4567")
check("Consumer Care: labelled toll-free phone", r.value == {"phone": "1800-123-4567", "email": None}, f"got {r.value}")

r = resolve_consumer_care("Consumer Care No. 9876543210")
check("Consumer Care: 'Consumer Care No.' + 10-digit phone",
      r.value == {"phone": "9876543210", "email": None}, f"got {r.value}")

r = resolve_consumer_care("Consumer Care Number: 9876543210")
check("Consumer Care: 'Consumer Care Number' label", r.value == {"phone": "9876543210", "email": None}, f"got {r.value}")

r = resolve_consumer_care("Customer Care No. 022-12345678")
check("Consumer Care: 'Customer Care No.' STD-code phone", r.value == {"phone": "022-12345678", "email": None}, f"got {r.value}")

r = resolve_consumer_care("Customer Support: 9876543210")
check("Consumer Care: 'Customer Support' label", r.value == {"phone": "9876543210", "email": None}, f"got {r.value}")

r = resolve_consumer_care("Contact Us support@abcfoods.com")
check("Consumer Care: 'Contact Us' + email", r.value == {"phone": None, "email": "support@abcfoods.com"}, f"got {r.value}")

r = resolve_consumer_care("Contact Details: care@abcfoods.com")
check("Consumer Care: 'Contact Details' label", r.value == {"phone": None, "email": "care@abcfoods.com"}, f"got {r.value}")

r = resolve_consumer_care("Consumer Care: 9876543210, care@abcfoods.com")
check("Consumer Care: both phone and email captured",
      r.value == {"phone": "9876543210", "email": "care@abcfoods.com"}, f"got {r.value}")

r = resolve_consumer_care("call 9876543210 for more info")
check("Consumer Care: unrelated phone with no label -> rejected", r.value is None, f"got {r.value}")

r = resolve_consumer_care("visit info@somecompany.com for jobs")
check("Consumer Care: unrelated email with no label -> rejected", r.value is None, f"got {r.value}")

r = resolve_consumer_care("Consumer Care")
check("Consumer Care: label with no phone/email nearby -> rejected", r.value is None, f"got {r.value}")

r = resolve_consumer_care("")
check("Consumer Care: empty input -> no crash", r.value is None, f"got {r.value}")


# ===========================================================================
# AUG 27 — Hindi + English keyword dictionary
# ===========================================================================

check("FIELD_KEYWORDS has Hindi MRP entry", FIELD_KEYWORDS["MRP"]["hi"] == ["अधिकतम खुदरा मूल्य"],
      f"got {FIELD_KEYWORDS['MRP']['hi']}")

r = resolve_mrp("अधिकतम खुदरा मूल्य ₹249")
check("Hindi MRP label resolves via raw text", r.value == 249.0, f"got {r.value}")

result = map_fields([
    {"text": "अधिकतम", "language": "hi"}, {"text": "खुदरा", "language": "hi"},
    {"text": "मूल्य", "language": "hi"}, {"text": "₹249", "language": "hi"},
])
check("Hindi MRP resolves via token list", result["MRP"]["value"] == 249.0, f"got {result['MRP']}")

result = map_fields([
    {"text": "MRP", "language": "en"}, {"text": "₹249", "language": "en"},
])
check("English MRP token language='en' still resolves", result["MRP"]["value"] == 249.0, f"got {result['MRP']}")

result = map_fields([
    {"text": "अधिकतम", "language": "hi"}, {"text": "खुदरा", "language": "hi"},
    {"text": "मूल्य", "language": "hi"}, {"text": "₹249", "language": "hi"},
    {"text": "Net", "language": "en"}, {"text": "Qty", "language": "en"},
    {"text": "500", "language": "en"}, {"text": "g", "language": "en"},
])
check("Mixed English/Hindi token list: Hindi MRP resolves", result["MRP"]["value"] == 249.0, f"got {result['MRP']}")
check("Mixed English/Hindi token list: English Net Qty resolves",
      result["NET_QUANTITY"]["value"] == {"amount": 500.0, "unit": "g"}, f"got {result['NET_QUANTITY']}")

r = resolve_manufacturer("निर्माता एबीसी फूड्स")
check("Unsupported Hindi manufacturer phrase remains unresolved (documented limitation)",
      r.value is None, f"got {r.value}")


# ===========================================================================
# AUG 27 — OCR metadata (bbox / confidence / language)
# ===========================================================================

full_tokens = [
    {"text": "Manufactured", "bbox": [[10, 30], [80, 30], [80, 40], [10, 40]], "confidence": 0.95, "language": "en"},
    {"text": "by", "bbox": [[82, 30], [95, 30], [95, 40], [82, 40]], "confidence": 0.95, "language": "en"},
    {"text": "ABC", "bbox": [[97, 30], [120, 30], [120, 40], [97, 40]], "confidence": 0.95, "language": "en"},
    {"text": "Foods", "bbox": [[122, 30], [150, 30], [150, 40], [122, 40]], "confidence": 0.95, "language": "en"},
]
result = map_fields(full_tokens)
mfr = result["MANUFACTURER_ADDRESS"]
check("OCR metadata: bbox preserved for supporting tokens", len(mfr["bbox"]) > 0, f"got {mfr['bbox']}")
check("OCR metadata: bbox uses given point order/shape",
      mfr["bbox"][0] == [[10, 30], [80, 30], [80, 40], [10, 40]], f"got {mfr['bbox'][0] if mfr['bbox'] else None}")
check("OCR metadata: language preserved as 'en'", mfr["language"] == "en", f"got {mfr['language']}")
check("OCR metadata: high OCR confidence keeps extraction confidence high",
      mfr["confidence"] == "high", f"got {mfr['confidence']}")

low_conf_tokens = [
    {"text": "Manufactured", "bbox": [[0, 0], [1, 1], [1, 1], [0, 0]], "confidence": 0.2, "language": "en"},
    {"text": "by", "bbox": [[0, 0], [1, 1], [1, 1], [0, 0]], "confidence": 0.2, "language": "en"},
    {"text": "ABC", "bbox": [[0, 0], [1, 1], [1, 1], [0, 0]], "confidence": 0.2, "language": "en"},
    {"text": "Foods", "bbox": [[0, 0], [1, 1], [1, 1], [0, 0]], "confidence": 0.2, "language": "en"},
]
result = map_fields(low_conf_tokens)
check("OCR metadata: low OCR confidence downgrades extraction confidence to low",
      result["MANUFACTURER_ADDRESS"]["confidence"] == "low", f"got {result['MANUFACTURER_ADDRESS']}")

result = map_fields([{"text": "Manufactured"}, {"text": "by"}, {"text": "ABC"}, {"text": "Foods"}])
check("OCR metadata: missing bbox/confidence/language -> no crash, empty bbox",
      result["MANUFACTURER_ADDRESS"]["bbox"] == [], f"got {result['MANUFACTURER_ADDRESS']['bbox']}")
check("OCR metadata: missing language defaults to 'en'",
      result["MANUFACTURER_ADDRESS"]["language"] == "en", f"got {result['MANUFACTURER_ADDRESS']['language']}")

malformed_tokens = [{"text": "MRP"}, {"confidence": 0.9}, {"text": None}, "not_a_dict", {"text": "₹249"}]
result = map_fields(malformed_tokens)
check("OCR metadata: malformed/missing-text tokens do not crash map_fields",
      result["MRP"]["value"] == 249.0, f"got {result['MRP']}")


# ===========================================================================
# AUG 27 — Structured output schema
# ===========================================================================

result = map_fields("MRP ₹249 Net Qty 500 g Manufactured by ABC Foods, Pune "
                     "Mfg Date 01/06/2026 Consumer Care 1800-123-4567")
for key in ("MRP", "NET_QUANTITY", "MANUFACTURER_ADDRESS", "MANUFACTURING_DATE", "CONSUMER_CARE"):
    check(f"Structured output: '{key}' present at top level", key in result, f"keys={list(result.keys())}")

check("Structured output: MRP resolves in combined string", result["MRP"]["value"] == 249.0, f"got {result['MRP']}")
check("Structured output: Net Qty resolves in combined string",
      result["NET_QUANTITY"]["value"] == {"amount": 500.0, "unit": "g"}, f"got {result['NET_QUANTITY']}")
check("Structured output: Manufacturer resolves in combined string",
      result["MANUFACTURER_ADDRESS"]["value"] == "ABC Foods, Pune", f"got {result['MANUFACTURER_ADDRESS']}")
check("Structured output: Mfg Date resolves in combined string",
      result["MANUFACTURING_DATE"]["value"] == "2026-06-01", f"got {result['MANUFACTURING_DATE']}")
check("Structured output: Consumer Care resolves in combined string",
      result["CONSUMER_CARE"]["value"] == {"phone": "1800-123-4567", "email": None}, f"got {result['CONSUMER_CARE']}")

result_empty = map_fields("MRP ₹249")
check("Structured output: unresolved new fields are None, not crashes/missing keys",
      result_empty["MANUFACTURER_ADDRESS"]["value"] is None
      and result_empty["MANUFACTURING_DATE"]["value"] is None
      and result_empty["CONSUMER_CARE"]["value"] is None,
      f"got {result_empty}")

import json as _json
try:
    _json.dumps(result)
    _json.dumps(result_empty)
    json_ok = True
except (TypeError, ValueError):
    json_ok = False
check("Structured output: full map_fields() result is JSON-serializable", json_ok)


# ===========================================================================
# AUG 27 CORRECTION — Bug 2: real calendar date validation
# ===========================================================================

r = resolve_manufacturing_date("Mfg Date 28/02/2026")
check("Calendar: 28/02/2026 valid (non-leap Feb, last real day)", r.value == "2026-02-28", f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 29/02/2024")
check("Calendar: 29/02/2024 valid (2024 is a leap year)", r.value == "2024-02-29", f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 29/02/2026")
check("Calendar: 29/02/2026 rejected (2026 is not a leap year)", r.value is None, f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 31/04/2026")
check("Calendar: 31/04/2026 rejected (April has 30 days)", r.value is None, f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 31/02/2026")
check("Calendar: 31/02/2026 rejected (February never has 31 days)", r.value is None, f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 32/01/2026")
check("Calendar: 32/01/2026 rejected (day out of range for any month)", r.value is None, f"got {r.value}")

r = resolve_manufacturing_date("Mfg Date 01/13/2026")
check("Calendar: 01/13/2026 rejected (month 13 does not exist)", r.value is None, f"got {r.value}")

# regression: previously-valid dates must still resolve after switching to
# real datetime.date validation
r = resolve_manufacturing_date("Mfg Date 01/06/2026")
check("Calendar regression: ordinary valid date still resolves", r.value == "2026-06-01", f"got {r.value}")
r = resolve_manufacturing_date("Mfg Date 15 Jun 2026")
check("Calendar regression: month-name format still resolves", r.value == "2026-06-15", f"got {r.value}")
r = resolve_manufacturing_date("Mfg Date 01/06/26")
check("Calendar regression: 2-digit-year policy unchanged (2000+YY)", r.value == "2026-06-01", f"got {r.value}")
check("Calendar regression: 2-digit-year still low confidence", r.confidence == "low", f"got {r.confidence}")


# ===========================================================================
# AUG 27 CORRECTION — Bug 1: OCR token metadata provenance after normalization
# ===========================================================================
# normalize_text() can change character-offset alignment (whitespace
# collapsing, "M . R . P ." -> "MRP", etc.). Each scenario below places a
# "noise" token BEFORE the real field whose text SHRINKS heavily under
# normalization (testing punctuation/abbreviation-driven length change),
# and gives the noise token a deliberately different bbox/language/
# confidence than the real field's tokens. The real field's label token
# also contains leading spaces and multiple internal spaces (testing
# whitespace-driven length change). A correct implementation must attach
# metadata from the REAL supporting tokens only — never the noise token's.

_NOISE_TOKEN = {"text": "M . R . P .", "bbox": [[0, 0], [5, 0], [5, 5], [0, 5]],
                 "confidence": 0.99, "language": "en"}

# --- Manufacturer/Address provenance ---
mfr_label_tok = {"text": "  Manufactured   by", "bbox": [[10, 0], [30, 0], [30, 5], [10, 5]],
                  "confidence": 0.15, "language": "hi"}
mfr_abc_tok = {"text": "ABC", "bbox": [[31, 0], [40, 0], [40, 5], [31, 5]],
               "confidence": 0.15, "language": "hi"}
mfr_foods_tok = {"text": "Foods", "bbox": [[41, 0], [55, 0], [55, 5], [41, 5]],
                  "confidence": 0.15, "language": "hi"}
result = map_fields([_NOISE_TOKEN, mfr_label_tok, mfr_abc_tok, mfr_foods_tok])
mfr = result["MANUFACTURER_ADDRESS"]
check("Provenance (Manufacturer): value extracted correctly despite upstream shrink",
      mfr["value"] == "ABC Foods", f"got {mfr['value']}")
check("Provenance (Manufacturer): language from REAL supporting tokens ('hi'), not noise token ('en')",
      mfr["language"] == "hi", f"got {mfr['language']}")
check("Provenance (Manufacturer): confidence downgraded from REAL tokens' 0.15, not noise's 0.99",
      mfr["confidence"] == "low", f"got {mfr['confidence']}")
check("Provenance (Manufacturer): bbox is exactly the real supporting tokens' boxes, "
      "in order, excluding the noise token's box",
      mfr["bbox"] == [mfr_label_tok["bbox"], mfr_abc_tok["bbox"], mfr_foods_tok["bbox"]],
      f"got {mfr['bbox']}")
check("Provenance (Manufacturer): noise token's bbox is NOT present in the result",
      _NOISE_TOKEN["bbox"] not in mfr["bbox"], f"got {mfr['bbox']}")

# --- Manufacturing Date provenance ---
date_label_tok = {"text": "  Mfg    Date", "bbox": [[10, 0], [30, 0], [30, 5], [10, 5]],
                   "confidence": 0.25, "language": "hi"}
date_value_tok = {"text": "01/06/2026", "bbox": [[31, 0], [50, 0], [50, 5], [31, 5]],
                   "confidence": 0.25, "language": "hi"}
result = map_fields([_NOISE_TOKEN, date_label_tok, date_value_tok])
mdate = result["MANUFACTURING_DATE"]
check("Provenance (Mfg Date): value extracted correctly despite upstream shrink",
      mdate["value"] == "2026-06-01", f"got {mdate['value']}")
check("Provenance (Mfg Date): language from REAL supporting tokens ('hi'), not noise token ('en')",
      mdate["language"] == "hi", f"got {mdate['language']}")
check("Provenance (Mfg Date): confidence downgraded from REAL tokens' 0.25, not noise's 0.99",
      mdate["confidence"] == "low", f"got {mdate['confidence']}")
check("Provenance (Mfg Date): bbox is exactly the real supporting tokens' boxes, excluding noise",
      mdate["bbox"] == [date_label_tok["bbox"], date_value_tok["bbox"]], f"got {mdate['bbox']}")
check("Provenance (Mfg Date): noise token's bbox is NOT present in the result",
      _NOISE_TOKEN["bbox"] not in mdate["bbox"], f"got {mdate['bbox']}")

# --- Consumer Care provenance ---
cc_label_tok = {"text": "  Consumer    Care", "bbox": [[10, 0], [30, 0], [30, 5], [10, 5]],
                "confidence": 0.20, "language": "hi"}
cc_value_tok = {"text": "1800-123-4567", "bbox": [[31, 0], [55, 0], [55, 5], [31, 5]],
                "confidence": 0.20, "language": "hi"}
result = map_fields([_NOISE_TOKEN, cc_label_tok, cc_value_tok])
cc = result["CONSUMER_CARE"]
check("Provenance (Consumer Care): value extracted correctly despite upstream shrink",
      cc["value"] == {"phone": "1800-123-4567", "email": None}, f"got {cc['value']}")
check("Provenance (Consumer Care): language from REAL supporting tokens ('hi'), not noise token ('en')",
      cc["language"] == "hi", f"got {cc['language']}")
check("Provenance (Consumer Care): confidence downgraded from REAL tokens' 0.20, not noise's 0.99",
      cc["confidence"] == "low", f"got {cc['confidence']}")
check("Provenance (Consumer Care): bbox is exactly the real supporting tokens' boxes, excluding noise",
      cc["bbox"] == [cc_label_tok["bbox"], cc_value_tok["bbox"]], f"got {cc['bbox']}")
check("Provenance (Consumer Care): noise token's bbox is NOT present in the result",
      _NOISE_TOKEN["bbox"] not in cc["bbox"], f"got {cc['bbox']}")

# --- sync-check: the map-tracking normalizer must never diverge from the
#     frozen normalize_text()'s actual output string ---
from app.field_mapping.field_mapping import normalize_text as _norm_frozen
from app.field_mapping.field_mapping import _normalize_text_with_map as _norm_mapped
for _s in ["MRP ₹249", "M . R . P . ₹249", "  Manufactured   by  ABC   Foods  ",
           "Net  Qty.   500g", "Rs. 249", "", "Mfg   Date 01/06/2026"]:
    _norm_text_only, _ = _norm_mapped(_s)
    check(f"Sync check: _normalize_text_with_map output matches frozen normalize_text for {_s!r}",
          _norm_text_only == _norm_frozen(_s), f"mapped={_norm_text_only!r} frozen={_norm_frozen(_s)!r}")


print("\n" + "=" * 60)
print(f"TOTAL: {PASS + FAIL}   PASSED: {PASS}   FAILED: {FAIL}")
if FAILURES:
    print("\nFailed tests:")
    for f in FAILURES:
        print(f"  - {f}")
print("=" * 60)

if __name__ == "__main__":
    sys.exit(0 if FAIL == 0 else 1)
