# Font-Size & Readability Module — Known Limitations

*Module: coin-based font-height calibration for Legal Metrology compliance checking*
*Status as of Aug 30: working prototype, integrated end-to-end*

## Accuracy

Validated at **8.0% mean measurement error** across 6 labeled real-world product photos spanning varied categories (liquid, cosmetics, ice cream, wafer, biscuits, chocolate). This is sufficient to flag clearly non-compliant font sizes but should not be treated as precise enough to serve as sole legal evidence in a borderline case.

A 7th test sample was excluded: the reference coin was photographed too close, with its edge never visible in frame. No calibration method can recover scale information the photo doesn't contain — this is a capture-quality constraint, not a fixable defect. It informs a practical guideline (the coin must be fully visible in the shot) rather than pointing to a code fix.

## Calibration approach trade-offs

The module uses a physical reference object (an Indian coin) placed in-frame to establish a pixel-to-millimeter scale, chosen over inferring scale from the package's own dimensions — the latter would require depth/perspective estimation that itself needs calibration, so it does not avoid the underlying problem.

This means:
- **A coin must be present in every photo.** This is a deliberate design trade-off, not an oversight.
- **The coin's approximate location is currently supplied manually** (a user tap), rather than auto-detected. A production version would need either automatic coin detection or a guided capture flow.

## Text orientation

The module currently assumes **upright, horizontal label text**. One test photo with rotated (sideways) text was excluded from validation, since the height-measurement logic assumes text height corresponds to the vertical axis of the bounding box, which does not hold for rotated text. Automatic rotation detection/correction was not implemented due to time constraints.

## Dependency on upstream OCR quality

During real end-to-end pipeline testing, standard printed label text produced usable results, but **stamped or dot-matrix MRP text (a common real-world labeling practice — ink-stamped prices, batch codes, and dates) consistently failed to produce usable OCR output** in testing (2 of 2 samples with stamped text). This is an upstream OCR limitation, not a defect in the font-size measurement logic itself: when OCR produces no usable text for a field, this module correctly returns no result for that field, rather than fabricating a measurement.

The team has identified this as a case for the separate LLM-assisted extraction fallback (planned for real-world text OCR struggles with), rather than requiring separate handling within this module.

## Regulatory data completeness

The minimum font-height thresholds used for compliance comparison are currently **placeholder values** pending confirmation of the exact slabs specified in the Legal Metrology (Packaged Commodities) Rules, 2011 from the team's Data & Rules Lead. The measurement pipeline itself is independent of this data and will produce correct results once real thresholds are supplied.

## Failure handling

In all identified failure modes above (no coin detected, rotated text, missing OCR data), the module is designed to return an explicit "unavailable" or "no result" response rather than a silently incorrect measurement or a crash. This was a deliberate design priority: an absent result is safer than a wrong one in a compliance-checking context.
