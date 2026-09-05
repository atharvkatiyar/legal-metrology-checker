# Font-Size & Readability Module — Known Limitations

*Module: coin-based font-height calibration for Legal Metrology compliance checking*
*Status as of Sep 4: working prototype, integrated end-to-end, tested against full sample set*

## Accuracy

Validated at **18.1% mean measurement error** across 12 labeled real-world product photos spanning varied categories (liquid, cosmetics, ice cream, wafer, biscuits, chocolate, chips, peanut butter, notebook, sauce, bread) and shooting conditions. This is sufficient to flag clearly non-compliant font sizes but should not be treated as precise enough to serve as sole legal evidence in a borderline case.

Three test samples were excluded across the full data collection, all for reasons unrelated to the calibration algorithm itself:
- One photo where the reference coin was photographed too close, with its edge never visible in frame — no calibration method can recover scale information the photo doesn't contain. This is a capture-quality constraint, not a fixable defect.
- One photo where the label text was at a visible angle, breaking the height-measurement logic's assumption of upright text (see Text Orientation section below).

**A key finding from the larger sample set**: measurement error correlates with the absolute size of the text being measured, not with any single failure in coin detection. Debug visualizations confirmed coin detection was accurate even on the highest-error samples (2-3mm text) — the error instead traces to manual bounding-box labeling precision (via the tap-to-label tool used to build this test set) mattering proportionally more on small targets. A 5-10 pixel labeling imprecision is negligible against a 90px-tall text region but becomes a large percentage of a 40-50px one. This is a genuine measurement-noise floor in the current manual labeling process, not a defect in the coin-detection or scale-calculation logic — worth noting since it would shrink substantially once bounding boxes come from the real OCR pipeline rather than manual clicks.

## Calibration approach trade-offs

The module uses a physical reference object (an Indian coin) placed in-frame to establish a pixel-to-millimeter scale, chosen over inferring scale from the package's own dimensions — the latter would require depth/perspective estimation that itself needs calibration, so it does not avoid the underlying problem.

This means:
- **A coin must be present in every photo.** This is a deliberate design trade-off, not an oversight.
- **The coin's approximate location is currently supplied manually** (a user tap), rather than auto-detected. A production version would need either automatic coin detection or a guided capture flow.

## Text orientation

The module currently assumes **upright, horizontal label text**. Two test photos with rotated/tilted text were excluded from validation (confirmed via an inverted bounding box on repeated independent measurements), since the height-measurement logic assumes text height corresponds to the vertical axis of the bounding box, which does not hold for rotated text. Automatic rotation detection/correction was not implemented due to time constraints.

## Dependency on upstream OCR quality

During real end-to-end pipeline testing, standard printed label text produced usable results, but **stamped or dot-matrix MRP text (a common real-world labeling practice — ink-stamped prices, batch codes, and dates) consistently failed to produce usable OCR output** in testing (2 of 2 samples with stamped text). This is an upstream OCR limitation, not a defect in the font-size measurement logic itself: when OCR produces no usable text for a field, this module correctly returns no result for that field, rather than fabricating a measurement.

The team has identified this as a case for the separate LLM-assisted extraction fallback (planned for real-world text OCR struggles with), rather than requiring separate handling within this module.

## Regulatory data completeness

The minimum font-height thresholds used for compliance comparison are currently **placeholder values** pending confirmation of the exact slabs specified in the Legal Metrology (Packaged Commodities) Rules, 2011 from the team's Data & Rules Lead. The measurement pipeline itself is independent of this data and will produce correct results once real thresholds are supplied.

## Failure handling

In all identified failure modes above (no coin detected, rotated text, missing OCR data), the module is designed to return an explicit "unavailable" or "no result" response rather than a silently incorrect measurement or a crash. This was a deliberate design priority: an absent result is safer than a wrong one in a compliance-checking context.
