import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.field_mapping import resolve_mrp, resolve_net_quantity, map_fields

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


# ---------------------------------------------------------------------------
# Case 1: MRP amid multiple conflicting monetary signals + a Net Qty field
# ---------------------------------------------------------------------------
s1 = "MRP ₹249 | Offer ₹199 | Save ₹50 | Net Qty 500 g"

r = resolve_mrp(s1)
check("Case1: MRP resolves to nearest/strongest (249), ignoring Offer/Save",
      r.value == 249.0, f"got {r.value}")

r = resolve_net_quantity(s1)
check("Case1: Net Qty resolves correctly amid MRP/Offer/Save noise",
      r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 2: Offer Price precedes everything; Net Qty in the middle; MRP last
# ---------------------------------------------------------------------------
s2 = "Offer Price ₹199 | Net Qty 500 g | MRP ₹249"

r = resolve_mrp(s2)
check("Case2: MRP not stolen by preceding unrelated Offer Price",
      r.value == 249.0, f"got {r.value}")

r = resolve_net_quantity(s2)
check("Case2: Net Qty unaffected by surrounding MRP/Offer context",
      r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 3: Serving Size + Protein near a genuine Net Weight declaration
# ---------------------------------------------------------------------------
s3 = "Serving Size 50 g | Protein 3 g | Net Weight 500 g"

r = resolve_net_quantity(s3)
check("Case3: Net Weight correctly isolated from Serving Size / Protein",
      r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

r = resolve_mrp(s3)
check("Case3: No MRP context at all -> no false MRP", r.value is None, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 4: Two equivalent MRP labels, same value -> not a real conflict
# ---------------------------------------------------------------------------
s4 = "MRP ₹249 | Maximum Retail Price ₹249"

r = resolve_mrp(s4)
check("Case4: Same value from two labels resolves cleanly to 249",
      r.value == 249.0, f"got {r.value}")
check("Case4: Not flagged ambiguous when values agree",
      r.ambiguous is False, f"got ambiguous={r.ambiguous}")

# ---------------------------------------------------------------------------
# Case 5: Two MRP labels, genuinely different values -> deterministic pick
# ---------------------------------------------------------------------------
s5 = "MRP ₹249 | MRP ₹299"

r = resolve_mrp(s5)
check("Case5: Deterministic pick (first/nearest = 249), not either value",
      r.value == 249.0, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 6: Two Net Qty labels, different values -> deterministic pick
# ---------------------------------------------------------------------------
s6 = "Net Qty 500 g | Net Weight 1 kg"

r = resolve_net_quantity(s6)
check("Case6: Deterministic pick (first/nearest = 500g)",
      r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 7: OCR-damaged currency symbol '?' co-occurring with Net Qty
# ---------------------------------------------------------------------------
s7 = "MRP ?249 | Net Qty 500g"

r = resolve_mrp(s7)
check("Case7: '?' OCR-recovery MRP resolves to 249", r.value == 249.0, f"got {r.value}")
check("Case7: '?' OCR-recovery forced to low confidence", r.confidence == "low", f"got {r.confidence}")

r = resolve_net_quantity(s7)
check("Case7: Net Qty unaffected by malformed MRP currency",
      r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 8: Label→value theft attempt — value sits closer to the WRONG label
# ---------------------------------------------------------------------------
s8 = "Special Price ₹150 MRP ₹249 Save ₹99"

r = resolve_mrp(s8)
check("Case8: MRP isolated between two negative-context prices",
      r.value == 249.0, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 9: amount-only MRP adjacent to a Net Qty label (no currency at all)
# ---------------------------------------------------------------------------
s9 = "MRP 249 Net Qty 500 g"

r = resolve_mrp(s9)
check("Case9: amount-only MRP still resolves (249) and doesn't grab Net Qty's 500",
      r.value == 249.0, f"got {r.value}")

r = resolve_net_quantity(s9)
check("Case9: Net Qty resolves correctly, unaffected by amount-only MRP",
      r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 10: dense real-label mixing all units + full pipeline via map_fields
# ---------------------------------------------------------------------------
s10 = ("Maximum Retail Price ₹499/- (incl. of all taxes) | "
       "Discount ₹50 | Net Weight 1.5 litres | Serving Size 250 ml")

result = map_fields(s10)
check("Case10: MRP resolves via full label despite trailing Discount",
      result["MRP"]["value"] == 499.0, f"got {result['MRP']}")
check("Case10: Net Weight resolves to 1.5 l despite nearby Serving Size",
      result["NET_QUANTITY"]["value"] == {"amount": 1.5, "unit": "l"},
      f"got {result['NET_QUANTITY']}")

# ---------------------------------------------------------------------------
# Case 11: zero / malformed adversarial values must never be accepted
# ---------------------------------------------------------------------------
r = resolve_mrp("MRP ₹0 Offer Price ₹199")
check("Case11: zero MRP rejected even with no other valid candidate",
      r.value is None, f"got {r.value}")

r = resolve_net_quantity("Net Qty 0 g Protein 5 g")
check("Case11: zero Net Qty rejected", r.value is None, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 12: numeric truncation + sign fallthrough, adjacent to Net Qty (Aug26c)
# ---------------------------------------------------------------------------
s12 = "MRP ₹2490 | Net Qty 500 g"

r = resolve_mrp(s12)
check("Case12: MRP not truncated to 249 in presence of other fields",
      r.value == 2490.0, f"got {r.value}")

r = resolve_net_quantity(s12)
check("Case12: Net Qty unaffected by MRP's larger numeric token",
      r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

s12b = "MRP ₹-249 | Net Qty 500 g"
r = resolve_mrp(s12b)
check("Case12b: negative MRP does not fall through to amount-only and grab 249",
      r.value is None, f"got {r.value}")

r = resolve_net_quantity(s12b)
check("Case12b: Net Qty still resolves correctly despite rejected negative MRP",
      r.value == {"amount": 500.0, "unit": "g"}, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 13: amount-only MRP false positive + Net Qty comma parsing (Aug26d)
# ---------------------------------------------------------------------------
s13 = "MRP abc249 | Net Qty 1,500 g"

r = resolve_mrp(s13)
check("Case13: amount-only MRP glued to letters rejected, doesn't leak into result",
      r.value is None, f"got {r.value}")

r = resolve_net_quantity(s13)
check("Case13: Net Qty comma-grouped value resolves correctly alongside rejected MRP",
      r.value == {"amount": 1500.0, "unit": "g"}, f"got {r.value}")

s13b = "MRP 249 | Net Qty 12,500 ml"
r = resolve_mrp(s13b)
check("Case13b: legitimate amount-only MRP still resolves", r.value == 249.0, f"got {r.value}")

r = resolve_net_quantity(s13b)
check("Case13b: Net Qty large comma-grouped value resolves correctly",
      r.value == {"amount": 12500.0, "unit": "ml"}, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 14: currency token boundary + strict comma grouping (Aug26e)
# ---------------------------------------------------------------------------
s14 = "MRP ₹249abc | Net Qty 1,23,45 g"

r = resolve_mrp(s14)
check("Case14: currency MRP glued to trailing letters rejected", r.value is None, f"got {r.value}")

r = resolve_net_quantity(s14)
check("Case14: Net Qty malformed comma grouping rejected alongside rejected MRP",
      r.value is None, f"got {r.value}")

s14b = "MRP ₹249.00abc | Net Qty 1,23,456 g"
r = resolve_mrp(s14b)
check("Case14b: decimal MRP glued to trailing letters rejected outright (not truncated to 249)",
      r.value is None, f"got {r.value}")

r = resolve_net_quantity(s14b)
check("Case14b: Net Qty valid Indian-style grouping resolves despite rejected MRP",
      r.value == {"amount": 123456.0, "unit": "g"}, f"got {r.value}")

s14c = "MRP ₹249 | Net Qty 12,3456 g"
r = resolve_mrp(s14c)
check("Case14c: legitimate currency MRP unaffected by neighboring malformed Net Qty",
      r.value == 249.0, f"got {r.value}")

r = resolve_net_quantity(s14c)
check("Case14c: Net Qty malformed 4-digit trailing group rejected", r.value is None, f"got {r.value}")

# ---------------------------------------------------------------------------
# Case 15: exact mixed-field cases from the Aug26f final-patch spec
# ---------------------------------------------------------------------------
r = map_fields("MRP ₹249abc | Net Qty 500 g")
check("Case15a: MRP None", r["MRP"]["value"] is None, f"got {r['MRP']}")
check("Case15a: Net Qty 500 g", r["NET_QUANTITY"]["value"] == {"amount": 500.0, "unit": "g"},
      f"got {r['NET_QUANTITY']}")

r = map_fields("MRP ₹249 | Net Qty 1,500 g")
check("Case15b: MRP 249", r["MRP"]["value"] == 249.0, f"got {r['MRP']}")
check("Case15b: Net Qty 1500 g", r["NET_QUANTITY"]["value"] == {"amount": 1500.0, "unit": "g"},
      f"got {r['NET_QUANTITY']}")

r = map_fields("MRP ₹1,23,45 | Net Qty 500 g")
check("Case15c: invalid MRP -> None", r["MRP"]["value"] is None, f"got {r['MRP']}")
check("Case15c: valid Net Qty 500 g", r["NET_QUANTITY"]["value"] == {"amount": 500.0, "unit": "g"},
      f"got {r['NET_QUANTITY']}")

# ---------------------------------------------------------------------------
# Case 16: final parser hardening regressions
# ---------------------------------------------------------------------------
r = resolve_mrp("MRP ₹249-abc | Net Qty 500 g")
check("Case16a: malformed MRP suffix rejected", r.value is None, f"got {r.value}")
r = resolve_net_quantity("MRP ₹249 | Net Qty 1.2.3 g")
check("Case16b: malformed quantity decimal rejected without partial match", r.value is None, f"got {r.value}")
r = resolve_net_quantity("MRP ₹249 | Net Qty 123,45,678 g")
check(
    "Case16c: valid Indian grouping with 3-digit leading group resolves",
    r.value == {"amount": 12345678.0, "unit": "g"},
    f"got {r.value}"
)
r = resolve_net_quantity("MRP ₹249 | Net Qty 1,23,45,678 g")
check("Case16d: valid long Indian grouping resolves", r.value == {"amount": 12345678.0, "unit": "g"}, f"got {r.value}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print("\n" + "=" * 60)
print(f"TOTAL: {PASS + FAIL}   PASSED: {PASS}   FAILED: {FAIL}")
if FAILURES:
    print("\nFailed tests:")
    for f in FAILURES:
        print(f"  - {f}")
print("=" * 60)

sys.exit(0 if FAIL == 0 else 1)
