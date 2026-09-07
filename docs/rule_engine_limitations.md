# Rule Engine & Compliance Logic Module — Known Limitations

*Module: presence and format validation of extracted label fields against Legal Metrology rules*
*Status as of Sep 4: working, integrated end-to-end into POST /scans/init, tested against all 13 labeled_batch samples*

## Test results

Tested against all 13 real product photos in `labeled_batch/`: **5 of 13 correctly evaluate as fully compliant** (score 100, zero violations), and every remaining violation corresponds to a genuinely missing or invalid field on that specific product. No crashes or unhandled exceptions across the full set.

## Coverage: presence vs. format checking

Every mandatory field (`mrp`, `net_quantity`, `manufacturer`, `mfg_date`, `consumer_care`) is checked for **presence**. Only two of these currently have machine-checkable **format** rules, because `rules.json` (Role 1's legal spec) only defines a `check_target` for these two:

- `net_quantity` — unit must be in the `valid_units` list (`g`, `kg`, `ml`, `l`, `ltr`, `pieces`, `units`, `cigarettes`, `sticks`, `pills`, `tablets`)
- `mrp` — originally had a regex requiring "inclusive of all taxes" wording; this was removed by Role 1 on Sep 4 (see "Data quality found" below), so `mrp` is currently presence-only like the rest

`manufacturer`, `mfg_date`, and `consumer_care` have no format rule at all in the current spec — only presence is checked. If Role 1 adds format rules for these later (e.g. a valid date-format regex for `mfg_date`), the module is structured so a matching `_check_x_format()` function can be added to `check_formats()` without touching the presence-checking logic.

## Data quality found in labeled_batch/ (resolved)

Cross-checking the Nescafé sample's JSON entry against the actual product photo found that the real label reads "MRP ₹10.00 (incl. of all taxes)" — fully compliant — but the JSON had only recorded "MRP ₹10.00", missing the tax declaration. This was a labeling gap, not a real violation, and it was surfaced because the (now-removed) MRP regex check flagged **all 13** samples identically, which was the signal that something systemic was wrong rather than 13 independent failures. The specific entry was corrected by the team on Sep 4; the underlying regex check was also removed as the more durable fix, since text-wording checks are brittle against real-world label variation (capitalization, phrasing, OCR line-breaks).

## Known open issue: unit list incomplete for non-metric products

`net_quantity`'s `valid_units` list does not include `"pair"`, so a product legitimately measured in pairs (tested case: footwear, "1 Pair") is incorrectly flagged as an invalid unit. Flagged to Role 1 (Sep 5); not yet fixed as of this writing. This module reads `valid_units` directly from `rules.json` at runtime, so no code change will be needed once the list is updated.

## Dependency on upstream Field Mapping normalization

Format checks assume Field Mapping (Role 3) has already normalized values into the expected shape — e.g. `net_quantity.normalized_value` as `{"amount": float, "unit": str}`. If Field Mapping's normalization ever changes shape (e.g. a different key name), the corresponding format check will silently treat the field as "could not be verified" rather than crash, but will not produce a meaningful violation until updated to match. This module does not re-derive normalization from `raw_value` itself, by design, to avoid duplicating logic that already lives in Field Mapping.

## Dependency on rules.json completeness

Severity levels, legal citations, and mandatory-field membership are all sourced directly from `rules.json` at runtime rather than hardcoded, so the module automatically reflects whatever Role 1's spec currently says — including gaps. `commodity_name` was removed from the mandatory list on Sep 4 to match `rules.json` being updated to drop it; if it returns to the spec later, `MANDATORY_FIELDS` will need a matching one-line update.

## Failure handling

If `rules.json` is missing or malformed, the module logs no error and simply produces zero format-check rules (graceful degradation to presence-only checking) rather than crashing the app. A missing field always produces `bbox=None` (nothing to draw a box around); a format violation on a present-but-invalid field carries the field's real `bbox`, so the frontend's red-box overlay can point at the exact incorrect text once implemented.