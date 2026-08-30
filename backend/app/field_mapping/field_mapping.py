"""
Field Mapping Engine — MRP & Net Quantity (English only)
SIH 2026 · PS #26034 · Aug 26 deliverable (hardened Aug 26b pass)

Pipeline: raw OCR text -> normalization -> label detection -> value
candidate extraction (boundary-aware) -> contextual + proximity scoring
-> ranked structured result.

Design notes:
- Deterministic, explainable, regex/keyword based (no ML).
- OCR schema is NOT assumed final. This module accepts either a plain
  string or a list of OCR "tokens" (dicts with at least a 'text' key,
  optionally 'bbox' / 'confidence'). See `map_fields()`.
- False positives are treated as worse than missed weak candidates:
  every accepted candidate must have supporting label context, and a
  label's value search is bounded by the START of the next label-like
  token (any positive or negative label), so one label can never scan
  across another label and steal its value.
"""

import re
import bisect
import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Union, Dict, Any


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Lightweight, non-destructive normalization for matching purposes.
    Original raw text is preserved separately by callers for evidence."""
    if not text:
        return ""
    t = text
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n+", " \n ", t)
    # NOTE: no trailing \s* before the final \b — a trailing \s* would greedily
    # consume the space *after* "MRP" (e.g. before "Rs 249"), merging tokens
    # into "MRPRs 249" and breaking downstream matching.
    t = re.sub(r"\bM\s*\.?\s*R\s*\.?\s*P\.?\b", "MRP", t, flags=re.IGNORECASE)
    t = re.sub(r"\bNet\s*Qty\.?\b", "Net Qty", t, flags=re.IGNORECASE)
    t = re.sub(r"\bNet\s*Wt\.?\b", "Net Wt", t, flags=re.IGNORECASE)
    t = re.sub(r"\bRs\s*\.\s*", "Rs ", t, flags=re.IGNORECASE)
    return t.strip()


# ---------------------------------------------------------------------------
# AUG 27 — normalization with character-level provenance map
# ---------------------------------------------------------------------------
# `normalize_text` above is frozen and untouched. This is a separate,
# additive function used ONLY by the new Aug 27 fields, needed because
# normalize_text's substitutions (whitespace collapsing, "M.R.P."->"MRP",
# "Net Qty."->"Net Qty", "Rs." -> "Rs ") can change text length, so a
# candidate's [start,end) offsets in the normalized text do NOT line up
# with the original OCR-token offsets from _build_token_index(). Applying
# them directly (the old behavior) could attach a neighboring token's
# bbox/language/confidence to the wrong field.
#
# This function mirrors normalize_text's exact substitution sequence
# (kept in sync manually — see the sync-check regression test) while also
# building `char_map`, where char_map[i] is the ORIGINAL-text character
# offset that produced normalized_text[i]. For a substituted/collapsed
# span, every output character maps to the START of the original span
# that produced it — sufficient to identify which original OCR token(s)
# a normalized span came from, which is all bbox/language/confidence
# attachment needs (token-level provenance, not exact character alignment).

def _sub_with_map(pattern: str, replacement: str, cur_text: str,
                   cur_map: List[int], flags: int = 0):
    """Apply one re.sub step while carrying a parallel offset map forward."""
    text_parts = []
    map_parts = []
    last_end = 0
    for m in re.finditer(pattern, cur_text, flags):
        text_parts.append(cur_text[last_end:m.start()])
        map_parts.append(cur_map[last_end:m.start()])
        origin = cur_map[m.start()] if m.start() < len(cur_map) else (
            cur_map[-1] + 1 if cur_map else 0)
        text_parts.append(replacement)
        map_parts.append([origin] * len(replacement))
        last_end = m.end()
    text_parts.append(cur_text[last_end:])
    map_parts.append(cur_map[last_end:])
    new_text = "".join(text_parts)
    new_map = [x for part in map_parts for x in part]
    return new_text, new_map


def _normalize_text_with_map(text: str):
    """Returns (normalized_text, char_map). Must stay behaviorally
    identical to normalize_text (same output string) — only adds
    provenance tracking on top."""
    if not text:
        return "", []
    t = text
    m = list(range(len(text)))
    t, m = _sub_with_map(r"[ \t]+", " ", t, m)
    t, m = _sub_with_map(r"\n+", " \n ", t, m)
    t, m = _sub_with_map(r"\bM\s*\.?\s*R\s*\.?\s*P\.?\b", "MRP", t, m, flags=re.IGNORECASE)
    t, m = _sub_with_map(r"\bNet\s*Qty\.?\b", "Net Qty", t, m, flags=re.IGNORECASE)
    t, m = _sub_with_map(r"\bNet\s*Wt\.?\b", "Net Wt", t, m, flags=re.IGNORECASE)
    t, m = _sub_with_map(r"\bRs\s*\.\s*", "Rs ", t, m, flags=re.IGNORECASE)
    # mirror t.strip()
    lstripped = t.lstrip()
    lstrip_len = len(t) - len(lstripped)
    stripped = lstripped.rstrip()
    final_map = m[lstrip_len: lstrip_len + len(stripped)]
    return stripped, final_map


def _map_norm_span_to_original(char_map: List[int], start: int, end: int,
                                orig_len: int):
    """Maps a [start, end) span in normalized-text coordinates back to a
    span in original-text coordinates, using the char_map produced by
    _normalize_text_with_map. Degrades gracefully to an empty/zero-width
    span (never crashes, never guesses a fixed offset) when the map is
    empty or out of range — this only happens when there was no text to
    begin with, in which case no candidate exists to look up anyway."""
    if not char_map or orig_len <= 0:
        return 0, 0
    n = len(char_map)
    s_idx = min(max(start, 0), n - 1)
    e_idx = min(max(end - 1, 0), n - 1)
    orig_start = char_map[s_idx]
    orig_end = char_map[e_idx] + 1
    if orig_end <= orig_start:
        orig_end = orig_start + 1
    orig_start = min(max(orig_start, 0), orig_len)
    orig_end = min(max(orig_end, orig_start + 1), orig_len)
    return orig_start, orig_end


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    field: str
    value: Any
    raw_evidence: str
    label_matched: str
    score: float
    start: int
    end: int
    reasons: List[str] = field(default_factory=list)       # human-readable trail
    reason_codes: List[str] = field(default_factory=list)  # concise codes
    suppressed: bool = False

    def to_dict(self):
        return {
            "field": self.field,
            "value": self.value,
            "raw_evidence": self.raw_evidence,
            "label_matched": self.label_matched,
            "score": self.score,
            "span": [self.start, self.end],
            "reasons": self.reasons,
            "reason_codes": self.reason_codes,
            "suppressed": self.suppressed,
        }


@dataclass
class FieldResult:
    field: str
    value: Optional[Any]
    confidence: str            # "high" | "low" | "none"
    raw_evidence: Optional[str]
    all_candidates: List[Dict[str, Any]]
    ambiguous: bool = False    # True when top candidates tie in score with differing values

    def to_dict(self):
        return {
            "field": self.field,
            "value": self.value,
            "confidence": self.confidence,
            "raw_evidence": self.raw_evidence,
            "ambiguous": self.ambiguous,
            "all_candidates": self.all_candidates,
        }


# ---------------------------------------------------------------------------
# Shared scoring constants (simple, deterministic, additive)
# ---------------------------------------------------------------------------

WINDOW = 40              # max char window a label may search for a value
AMOUNT_ONLY_WINDOW = 15  # tight window for currency-less "MRP 249" fallback

LABEL_BASE = 0.5
CURRENCY_BONUS = 0.3
OCR_RECOVERY_BONUS = 0.15
AMOUNT_ONLY_BONUS = 0.1
PROXIMITY_MAX_BONUS = 0.2
PROXIMITY_DECAY_PER_CHAR = 0.02  # bonus reaches 0 by ~10 chars distance

HIGH_CONFIDENCE_THRESHOLD = 0.9


def _proximity_bonus(distance: int) -> float:
    """Adjacent value = strongest, farther valid value = weaker. Simple
    linear decay, floors at 0."""
    return max(0.0, round(PROXIMITY_MAX_BONUS - PROXIMITY_DECAY_PER_CHAR * distance, 4))


# ---------------------------------------------------------------------------
# MRP config
# ---------------------------------------------------------------------------

MRP_LABELS = [
    (r"\bMaximum\s+Retail\s+Price\b", "Maximum Retail Price"),
    (r"\bMRP\b", "MRP"),
    # Aug 27 addition: Hindi MRP label, purely additive — same downstream
    # currency/amount regex handles the value after either an English or
    # Hindi label, so no other MRP logic changes. Digits/currency symbols
    # are unaffected by language.
    (r"अधिकतम\s*खुदरा\s*मूल्य", "Maximum Retail Price (Hindi)"),
]

MRP_NEGATIVE = [
    r"\bOffer\s*Price\b", r"\bOffer\b", r"\bDiscount\b", r"\bSave\b",
    r"\bSpecial\s*Price\b", r"\bYou\s*Pay\b",
]

CURRENCY_AMOUNT_RE = re.compile(
    r"(?P<sign1>[+-])?\s*"
    r"(?P<currency>₹|Rs\.?|INR|\?)\s*"
    r"(?P<sign2>[+-])?\s*"
    r"(?P<amount_raw>\d[\d,]*(?:\.\d+)?)"
    r"(?P<slash>/-)?"
    r"(?P<trailing_junk>[A-Za-z]+)?"
    r"(?![A-Za-z0-9_]|-\w|%\w)",  # captures letters glued directly onto
    # the number/slash so the whole candidate can be rejected outright in
    # Python, rather than letting the optional decimal group backtrack and
    # silently accept a truncated integer (e.g. "249.00abc" -> "249").
    re.IGNORECASE,
)

# Amount-only fallback token. Anchored (used with .match, not .search) so
# only whitespace/colon may sit between the label and the number — this
# rejects arbitrary alphanumeric attachment like "MRP abc249" or
# "MRPabc249" rather than scanning ahead and grabbing an embedded digit
# run. Trailing (?!\w) similarly blocks a number glued to trailing letters.
AMOUNT_ONLY_RAW_RE = re.compile(
    r"[\s:]*(?P<sign>[+-])?(?P<amount_raw>\d[\d,]*(?:\.\d+)?)(?!\w)"
)

# Narrow OCR recovery for product labels where EasyOCR renders a currency
# marker such as "R=" followed by a split decimal value:
# "R= .301. 00" -> "301.00"
#
# This pattern is deliberately narrow. It is used only by the dedicated
# MRP OCR-recovery path below and does not replace the normal currency matcher.
OCR_MRP_SPLIT_DECIMAL_RE = re.compile(
    r"R=\s*\.(?P<int_part>\d[\d,]*)\.\s*(?P<dec_part>\d{2})",
    re.IGNORECASE,
)

# The normal MRP search remains bounded by WINDOW. This separate recovery
# window is only for the exact OCR corruption handled by
# OCR_MRP_SPLIT_DECIMAL_RE.
OCR_MRP_RECOVERY_WINDOW = 100


# ---------------------------------------------------------------------------
# Net Quantity config
# ---------------------------------------------------------------------------

NET_QTY_LABELS = [
    (r"\bNet\s*Quantity\b", "Net Quantity"),
    (r"\bNet\s*Qty\b", "Net Qty"),
    (r"\bNet\s*Weight\b", "Net Weight"),
    (r"\bNet\s*Wt\b", "Net Wt"),
    (r"\bNet\s*Volume\b", "Net Volume"),
    (r"\bNet\s*Vol\b", "Net Vol"),
]

NET_QTY_NEGATIVE = [
    r"\bProtein\b", r"\bFat\b", r"\bCarbohydrate\b", r"\bServing\s*Size\b",
    r"\bPer\s*Serving\b", r"\bEnergy\b", r"\bCalories\b", r"\bPack\s*of\b",
    r"\bServings?\b",
]

UNIT_RE = (
    r"(milligrams?|kilograms?|millilitres?|milliliters?|litres?|liters?|"
    r"grams?|mg|kg|g|ml|mL|ML|l|L)"
)
QUANTITY_VALUE_RE = re.compile(
    r"(?<![\d.,])(?P<sign>[+-])?\s*(?P<amount_raw>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<unit>" + UNIT_RE + r")\b",
    re.IGNORECASE,
)

UNIT_NORMALIZE = {
    "l": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "ml": "ml", "millilitre": "ml", "millilitres": "ml",
    "milliliter": "ml", "milliliters": "ml",
    "g": "g", "gram": "g", "grams": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
}


def _normalize_unit(u: str) -> str:
    return UNIT_NORMALIZE.get(u.lower(), u.lower())


# Strict integer-part grouping. Supports:
#   - a plain unbroken digit run (no commas at all): 2490
#   - international grouping, every group after the first exactly 3 digits: 1,234,567
#   - Indian/lakh-crore grouping: 1-3 leading digits, then 1+ groups of
#     exactly 2 digits, then a final group of exactly 3 digits: 1,23,456 / 12,34,567
# Anything that doesn't fit one of these exactly (e.g. "1,23,45", "123,45",
# "12,3456") is rejected as a whole — never partially matched.
_INTEGER_STRICT_RE = re.compile(
    r"^(?:"
    r"\d{1,3}(?:,\d{3})+"          # international: 1,234 / 1,234,567
    r"|\d{1,3}(?:,\d{2})+,\d{3}"   # Indian: 1,23,456 / 12,34,567 / 123,45,678
    r"|\d+"                        # plain digit run, no commas
    r")$"
)


def _parse_amount_token(raw: Optional[str]) -> Optional[float]:
    """Parse a captured numeric token (e.g. '1,249', '2490', '249.99') into
    a float, or return None if malformed.

    Never truncates: a token with an invalid decimal part (3+ digits) or
    invalid comma grouping is rejected outright rather than accepting a
    shorter "valid-looking" prefix of it. Also rejects non-positive values."""
    if not raw:
        return None
    if "." in raw:
        int_part, _, dec_part = raw.partition(".")
    else:
        int_part, dec_part = raw, ""
    if not int_part or not _INTEGER_STRICT_RE.match(int_part):
        return None
    if dec_part:
        if not dec_part.isdigit() or len(dec_part) > 2:
            return None  # malformed / overlong decimal -> reject whole token
    cleaned = int_part.replace(",", "") + (("." + dec_part) if dec_part else "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value <= 0:
        return None  # sanity check: reject zero/negative
    return value


def _parse_quantity_amount_token(raw: Optional[str]) -> Optional[float]:
    """Parse a captured Net Quantity numeric token, supporting comma-grouped
    values (e.g. '1,500' -> 1500.0) via the same strict integer-grouping
    rule as MRP, so malformed grouping is rejected rather than partially
    matched (the '1,500 g' -> '500 g' bug). Unlike MRP amounts, quantity
    decimals are NOT capped at 2 digits — that restriction was never part
    of the original Net Quantity behavior and this fix preserves it."""
    if not raw:
        return None
    if "." in raw:
        int_part, _, dec_part = raw.partition(".")
    else:
        int_part, dec_part = raw, ""
    if not int_part or not _INTEGER_STRICT_RE.match(int_part):
        return None  # malformed comma grouping -> reject whole token, never truncate
    if dec_part and not dec_part.isdigit():
        return None
    cleaned = int_part.replace(",", "") + (("." + dec_part) if dec_part else "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _boundaries(text: str, positive_patterns, negative_patterns) -> List[int]:
    """Start positions of every label-like token (positive or negative) for
    a field, sorted. Used to stop one label's value search before it can
    scan across another strong label / conflicting label."""
    starts = []
    for pat, _name in positive_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            starts.append(m.start())
    for pat in negative_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            starts.append(m.start())
    starts.sort()
    return starts


def _effective_window_end(text: str, label_start: int, search_start: int,
                           boundary_starts: List[int], max_span: int) -> int:
    """Value search for this label occurrence must stop at whichever comes
    first: max_span chars, end of text, or the next label-like boundary."""
    idx = bisect.bisect_right(boundary_starts, label_start)
    next_boundary = boundary_starts[idx] if idx < len(boundary_starts) else len(text)
    return min(search_start + max_span, next_boundary, len(text))


def _has_within_span(patterns: List[str], text: str, start: int, end: int) -> Optional[str]:
    """Negative-keyword check restricted to the candidate's own
    label->value evidence span only (not the whole line) — an unrelated
    negative keyword elsewhere in the text must not suppress a separate,
    valid candidate."""
    region = text[start:end]
    for pat in patterns:
        m = re.search(pat, region, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def _select_best(candidates: List[Candidate]) -> (Optional[Candidate], bool):
    """Deterministic selection: highest score wins; ties broken by earliest
    position in text (first/nearest label claims it). Returns (best, ambiguous)."""
    valid = [c for c in candidates if not c.suppressed]
    if not valid:
        return None, False
    ranked = sorted(valid, key=lambda c: (-c.score, c.start))
    best = ranked[0]
    ambiguous = False
    if len(ranked) > 1:
        second = ranked[1]
        if abs(second.score - best.score) < 1e-9 and second.value != best.value:
            ambiguous = True
    if "NEAREST_VALUE" not in best.reason_codes:
        best.reason_codes.append("NEAREST_VALUE")
        best.reasons.append("selected: highest-scoring / nearest valid candidate")
    return best, ambiguous


# ---------------------------------------------------------------------------
# MRP extraction
# ---------------------------------------------------------------------------

def extract_mrp_candidates(text: str) -> List[Candidate]:
    candidates: List[Candidate] = []
    if not text:
        return candidates

    boundary_starts = _boundaries(text, MRP_LABELS, MRP_NEGATIVE)

    for label_pat, label_name in MRP_LABELS:
        for lm in re.finditer(label_pat, text, re.IGNORECASE):
            search_start = lm.end()
            eff_end = _effective_window_end(text, lm.start(), search_start,
                                             boundary_starts, WINDOW)
            window_text = text[search_start:eff_end]

            found_currency_match = False

            for vm in CURRENCY_AMOUNT_RE.finditer(window_text):
                # A currency symbol was found here at all (regardless of
                # whether the number turns out valid) — this must block the
                # amount-only fallback below, so a rejected "MRP ₹-249"
                # never falls through and gets silently re-read as "249".
                found_currency_match = True

                abs_start = search_start + vm.start()
                abs_end = search_start + vm.end()
                currency_sym = vm.group("currency")
                sign_present = bool(vm.group("sign1") or vm.group("sign2"))
                trailing_junk = vm.group("trailing_junk")
                amount = _parse_amount_token(vm.group("amount_raw"))

                if sign_present:
                    reasons = [f"label='{label_name}'",
                               "explicit +/- sign adjacent to currency value -> rejected (signed values not accepted)"]
                    candidates.append(Candidate(
                        field="MRP", value=amount,  # magnitude kept for debug only
                        raw_evidence=text[lm.start():abs_end],
                        label_matched=label_name, score=0.0,
                        start=lm.start(), end=abs_end,
                        reasons=reasons,
                        reason_codes=["MRP_LABEL_MATCH", "NEGATIVE_OR_SIGNED_REJECTED"],
                        suppressed=True,
                    ))
                    continue

                if trailing_junk:
                    # Letters glued directly onto the number/slash (e.g.
                    # "249abc", "249.00abc") invalidate the WHOLE token —
                    # reject outright rather than falling back to the
                    # integer prefix that happened to parse.
                    reasons = [f"label='{label_name}'",
                               f"trailing alphabetic characters '{trailing_junk}' glued to value -> rejected, not truncated"]
                    candidates.append(Candidate(
                        field="MRP", value=amount,  # magnitude kept for debug only
                        raw_evidence=text[lm.start():abs_end],
                        label_matched=label_name, score=0.0,
                        start=lm.start(), end=abs_end,
                        reasons=reasons,
                        reason_codes=["MRP_LABEL_MATCH", "TRAILING_ALPHA_REJECTED"],
                        suppressed=True,
                    ))
                    continue

                if amount is None:
                    reasons = [f"label='{label_name}'",
                               "malformed numeric token (invalid decimal length or comma grouping) -> rejected, not truncated"]
                    candidates.append(Candidate(
                        field="MRP", value=None,
                        raw_evidence=text[lm.start():abs_end],
                        label_matched=label_name, score=0.0,
                        start=lm.start(), end=abs_end,
                        reasons=reasons,
                        reason_codes=["MRP_LABEL_MATCH", "MALFORMED_NUMERIC_REJECTED"],
                        suppressed=True,
                    ))
                    continue

                distance = vm.start()  # chars between label end and value start
                prox = _proximity_bonus(distance)

                reasons = [f"label='{label_name}'", f"distance={distance}"]
                reason_codes = ["MRP_LABEL_MATCH"]
                is_ocr_recovery = (currency_sym == "?")

                if is_ocr_recovery:
                    score = LABEL_BASE + OCR_RECOVERY_BONUS + prox
                    reasons.append("malformed '?' currency accepted only due to adjacent MRP label")
                    reason_codes.append("OCR_CURRENCY_RECOVERY")
                else:
                    score = LABEL_BASE + CURRENCY_BONUS + prox
                    reasons.append(f"currency symbol '{currency_sym}' recognized")
                    reason_codes.append("CURRENCY_MATCH")

                if vm.group("slash"):
                    reasons.append("trailing '/-' stripped")

                neg = _has_within_span(MRP_NEGATIVE, text, lm.start(), abs_end)
                suppressed = neg is not None
                if suppressed:
                    reasons.append(f"suppressed: conflicting keyword '{neg}' within evidence span")
                    reason_codes.append("CONFLICT_SUPPRESSED")

                cand = Candidate(
                    field="MRP", value=amount,
                    raw_evidence=text[lm.start():abs_end],
                    label_matched=label_name, score=round(score, 4),
                    start=lm.start(), end=abs_end,
                    reasons=reasons, reason_codes=reason_codes,
                    suppressed=suppressed,
                )
                if is_ocr_recovery:
                    cand.reasons.append("confidence forced 'low' (OCR recovery)")
                candidates.append(cand)

            # Narrow OCR recovery: EasyOCR can render an MRP currency/value
            # such as "R= .301. 00". The normal MRP search window remains
            # unchanged; this dedicated recovery path gets its own bounded
            # extended window so OCR-corrupted values farther from the label
            # can still be recovered without widening normal MRP matching.
            if not found_currency_match:
                recovery_end = min(
                    len(text),
                    search_start + OCR_MRP_RECOVERY_WINDOW,
                )
                recovery_window_text = text[search_start:recovery_end]

                for ocr_match in OCR_MRP_SPLIT_DECIMAL_RE.finditer(
                    recovery_window_text
                ):
                    recovered_raw = (
                        f"{ocr_match.group('int_part')}."
                        f"{ocr_match.group('dec_part')}"
                    )
                    recovered_amount = _parse_amount_token(recovered_raw)

                    if recovered_amount is None:
                        continue

                    abs_start = search_start + ocr_match.start()
                    abs_end = search_start + ocr_match.end()
                    distance = ocr_match.start()
                    prox = _proximity_bonus(distance)

                    reasons = [
                        f"label='{label_name}'",
                        f"distance={distance}",
                        "OCR split-decimal currency recovery",
                        f"recovered numeric token '{recovered_raw}'",
                    ]

                    reason_codes = [
                        "MRP_LABEL_MATCH",
                        "OCR_CURRENCY_RECOVERY",
                    ]

                    candidates.append(
                        Candidate(
                            field="MRP",
                            value=recovered_amount,
                            raw_evidence=text[lm.start():abs_end],
                            label_matched=label_name,
                            score=round(
                                LABEL_BASE + OCR_RECOVERY_BONUS + prox,
                                4,
                            ),
                            start=lm.start(),
                            end=abs_end,
                            reasons=reasons,
                            reason_codes=reason_codes,
                            suppressed=False,
                        )
                    )

                    # A successful OCR-recovery candidate means we should not
                    # fall through and grab an unrelated distant number via
                    # the amount-only fallback.
                    found_currency_match = True
                    break

            # Amount-only fallback: only when no currency-symbol match was
            # found in this label's window AND the number sits immediately
            # (tightly) next to the label — never a bare/distant number.
            if not found_currency_match:
                tight_end = _effective_window_end(text, lm.start(), search_start,
                                                    boundary_starts, AMOUNT_ONLY_WINDOW)
                tight_text = text[search_start:tight_end]
                m = AMOUNT_ONLY_RAW_RE.match(tight_text)
                if m:
                    sign_present = bool(m.group("sign"))
                    amount = _parse_amount_token(m.group("amount_raw"))
                    # value_pos: where the sign/digits actually start, not
                    # the leading whitespace/colon consumed by the match.
                    value_pos = m.start("sign") if sign_present else m.start("amount_raw")
                    abs_start = search_start + value_pos
                    abs_end = search_start + m.end()

                    if sign_present:
                        reasons = [f"label='{label_name}'",
                                   "explicit +/- sign adjacent to amount-only value -> rejected (signed values not accepted)"]
                        candidates.append(Candidate(
                            field="MRP", value=amount,  # magnitude kept for debug only
                            raw_evidence=text[lm.start():abs_end],
                            label_matched=label_name, score=0.0,
                            start=lm.start(), end=abs_end,
                            reasons=reasons,
                            reason_codes=["MRP_LABEL_MATCH", "AMOUNT_ONLY_FALLBACK", "NEGATIVE_OR_SIGNED_REJECTED"],
                            suppressed=True,
                        ))
                    elif amount is None:
                        reasons = [f"label='{label_name}'",
                                   "malformed numeric token in amount-only fallback -> rejected, not truncated"]
                        candidates.append(Candidate(
                            field="MRP", value=None,
                            raw_evidence=text[lm.start():abs_end],
                            label_matched=label_name, score=0.0,
                            start=lm.start(), end=abs_end,
                            reasons=reasons,
                            reason_codes=["MRP_LABEL_MATCH", "AMOUNT_ONLY_FALLBACK", "MALFORMED_NUMERIC_REJECTED"],
                            suppressed=True,
                        ))
                    else:
                        distance = value_pos
                        prox = _proximity_bonus(distance)
                        score = LABEL_BASE + AMOUNT_ONLY_BONUS + prox
                        reasons = [f"label='{label_name}'", f"distance={distance}",
                                   "no currency symbol present — amount-only fallback"]
                        reason_codes = ["MRP_LABEL_MATCH", "AMOUNT_ONLY_FALLBACK"]

                        neg = _has_within_span(MRP_NEGATIVE, text, lm.start(), abs_end)
                        suppressed = neg is not None
                        if suppressed:
                            reasons.append(f"suppressed: conflicting keyword '{neg}' within evidence span")
                            reason_codes.append("CONFLICT_SUPPRESSED")

                        candidates.append(Candidate(
                            field="MRP", value=amount,
                            raw_evidence=text[lm.start():abs_end],
                            label_matched=label_name, score=round(score, 4),
                            start=lm.start(), end=abs_end,
                            reasons=reasons, reason_codes=reason_codes,
                            suppressed=suppressed,
                        ))

    return candidates


def resolve_mrp(text: str) -> FieldResult:
    norm = normalize_text(text)
    candidates = extract_mrp_candidates(norm)
    best, ambiguous = _select_best(candidates)

    if best is None:
        return FieldResult("MRP", None, "none", None,
                            [c.to_dict() for c in candidates], ambiguous=False)

    is_ocr_recovery = "OCR_CURRENCY_RECOVERY" in best.reason_codes
    is_amount_only = "AMOUNT_ONLY_FALLBACK" in best.reason_codes
    if is_ocr_recovery or is_amount_only:
        confidence = "low"
    else:
        confidence = "high" if best.score >= HIGH_CONFIDENCE_THRESHOLD else "low"

    return FieldResult("MRP", best.value, confidence, best.raw_evidence,
                        [c.to_dict() for c in candidates], ambiguous=ambiguous)


# ---------------------------------------------------------------------------
# Net Quantity extraction
# ---------------------------------------------------------------------------

def extract_net_qty_candidates(text: str) -> List[Candidate]:
    candidates: List[Candidate] = []
    if not text:
        return candidates

    boundary_starts = _boundaries(text, NET_QTY_LABELS, NET_QTY_NEGATIVE)

    for label_pat, label_name in NET_QTY_LABELS:
        for lm in re.finditer(label_pat, text, re.IGNORECASE):
            search_start = lm.end()
            eff_end = _effective_window_end(text, lm.start(), search_start,
                                             boundary_starts, WINDOW)
            window_text = text[search_start:eff_end]

            for vm in QUANTITY_VALUE_RE.finditer(window_text):
                abs_start = search_start + vm.start()
                abs_end = search_start + vm.end()
                sign_present = bool(vm.group("sign"))
                unit_raw = vm.group("unit")
                unit = _normalize_unit(unit_raw)

                if sign_present:
                    reasons = [f"label='{label_name}'",
                               "explicit +/- sign adjacent to quantity value -> rejected (signed values not accepted)"]
                    candidates.append(Candidate(
                        field="NET_QUANTITY", value=None,
                        raw_evidence=text[lm.start():abs_end],
                        label_matched=label_name, score=0.0,
                        start=lm.start(), end=abs_end,
                        reasons=reasons,
                        reason_codes=["NET_QTY_LABEL_MATCH", "NEGATIVE_OR_SIGNED_REJECTED"],
                        suppressed=True,
                    ))
                    continue

                try:
                    amount = _parse_quantity_amount_token(vm.group("amount_raw"))
                except ValueError:
                    continue
                if amount is None or amount <= 0:
                    continue  # sanity check: reject zero/malformed/malformed-grouping

                distance = vm.start()
                prox = _proximity_bonus(distance)
                score = LABEL_BASE + AMOUNT_ONLY_BONUS + prox + 0.2  # unit match bonus (fixed 0.2, always present)

                reasons = [f"label='{label_name}'", f"distance={distance}",
                           f"unit '{unit_raw}' normalized to '{unit}'"]
                reason_codes = ["NET_QTY_LABEL_MATCH", "UNIT_MATCH"]

                neg = _has_within_span(NET_QTY_NEGATIVE, text, lm.start(), abs_end)
                suppressed = neg is not None
                if suppressed:
                    reasons.append(f"suppressed: conflicting keyword '{neg}' within evidence span")
                    reason_codes.append("CONFLICT_SUPPRESSED")

                candidates.append(Candidate(
                    field="NET_QUANTITY", value={"amount": amount, "unit": unit},
                    raw_evidence=text[lm.start():abs_end],
                    label_matched=label_name, score=round(score, 4),
                    start=lm.start(), end=abs_end,
                    reasons=reasons, reason_codes=reason_codes,
                    suppressed=suppressed,
                ))

    return candidates


def resolve_net_quantity(text: str) -> FieldResult:
    norm = normalize_text(text)
    candidates = extract_net_qty_candidates(norm)
    best, ambiguous = _select_best(candidates)

    if best is None:
        return FieldResult("NET_QUANTITY", None, "none", None,
                            [c.to_dict() for c in candidates], ambiguous=False)

    confidence = "high" if best.score >= HIGH_CONFIDENCE_THRESHOLD else "low"
    return FieldResult("NET_QUANTITY", best.value, confidence, best.raw_evidence,
                        [c.to_dict() for c in candidates], ambiguous=ambiguous)


# =============================================================================
# AUG 27 — Manufacturer/Address, Manufacturing Date, Consumer Care
# =============================================================================
#
# Additive only. Nothing above this block (MRP / Net Quantity) is modified,
# except one purely-additive Hindi MRP label tuple (see MRP_LABELS above),
# which cannot change English-only behavior since it only adds a new regex
# alternative that matches Devanagari text no existing test contains.
#
# These new fields reuse the existing Candidate / FieldResult architecture:
# boundary-aware label->value search (_boundaries / _effective_window_end),
# negative-context suppression (_has_within_span), and deterministic
# selection (_select_best). No new scoring system is introduced.

from collections import Counter as _Counter


# ---------------------------------------------------------------------------
# Centralized field keyword dictionary (English + Hindi)
# ---------------------------------------------------------------------------
# Reference/documentation dictionary organized by field and language. The
# regex label lists below are the authoritative matching patterns; this
# dict mirrors them for a single place downstream engineers can inspect
# supported keywords per field/language. Hindi entries are added ONLY where
# a real, verified translation was supplied — inventing translations for
# fields with no supplied Hindi phrase would violate the "no unsupported
# Hindi coverage" requirement, so those lists stay empty (English-only)
# and unresolved Hindi text for those fields is a genuine limitation.
FIELD_KEYWORDS = {
    "MRP": {
        "en": ["MRP", "M.R.P.", "Maximum Retail Price"],
        "hi": ["अधिकतम खुदरा मूल्य"],
    },
    "NET_QUANTITY": {
        "en": ["Net Qty", "Net Quantity", "Net Wt", "Net Weight"],
        "hi": [],
    },
    "MANUFACTURER_ADDRESS": {
        "en": ["Manufactured by", "Manufacturer", "Marketed by", "Manufactured for",
               "Mfd. by", "Mfg. by", "Manufactured & Marketed by",
               "Manufactured and Marketed by"],
        "hi": [],
    },
    "MANUFACTURING_DATE": {
        "en": ["Mfg Date", "Mfd Date", "Manufacturing Date", "Manufactured Date",
               "Date of Manufacture", "Packed On", "Packing Date"],
        "hi": [],
    },
    "CONSUMER_CARE": {
        "en": ["Consumer Care", "Consumer Care Details", "Consumer Care No.",
               "Consumer Care Number", "Customer Care", "Customer Care No.",
               "Customer Care Number", "Customer Support", "Contact Us",
               "Contact Details"],
        "hi": [],
    },
}


# ---------------------------------------------------------------------------
# Extended field result (adds language + bbox evidence on top of FieldResult)
# ---------------------------------------------------------------------------

@dataclass
class ExtendedFieldResult:
    field: str
    value: Optional[Any]
    confidence: str
    raw_evidence: Optional[str]
    language: Optional[str]
    bbox: List[Any]
    ambiguous: bool
    all_candidates: List[Dict[str, Any]]

    def to_dict(self):
        return {
            "field": self.field,
            "value": self.value,
            "confidence": self.confidence,
            "raw_evidence": self.raw_evidence,
            "language": self.language,
            "bbox": self.bbox,
            "ambiguous": self.ambiguous,
            "all_candidates": self.all_candidates,
        }


# ---------------------------------------------------------------------------
# OCR token indexing (bbox / confidence / language plumbing)
# ---------------------------------------------------------------------------

def _build_token_index(ocr_input: Union[str, List[Dict[str, Any]], None]):
    """Returns (text, tokens).

    `text` is built with the exact same join logic as `_coerce_to_text`
    (single-space join of each token's 'text', skipping non-dict tokens or
    missing/None text) so char offsets used here line up with the text the
    frozen MRP/Net Quantity extractors already operate on.

    `tokens` is a list of {"start","end","text","bbox","confidence",
    "language"} dicts giving each token's character span in `text`. For a
    raw string input, or when metadata is absent, tokens is simply []
    (or has None metadata fields) — callers must not crash on that.
    """
    if isinstance(ocr_input, str):
        return ocr_input, []
    if not isinstance(ocr_input, list):
        return "", []

    tokens = []
    parts = []
    cursor = 0
    for tok in ocr_input:
        if not isinstance(tok, dict):
            continue
        raw_text = tok.get("text")
        if raw_text is None:
            continue
        s = str(raw_text)
        if parts:
            cursor += 1  # the single space that will join this token on
        start = cursor
        end = start + len(s)
        tokens.append({
            "start": start,
            "end": end,
            "text": s,
            "bbox": tok.get("bbox"),
            "confidence": tok.get("confidence"),
            "language": tok.get("language"),
        })
        parts.append(s)
        cursor = end
    text = " ".join(parts)
    return text, tokens


def _supporting_tokens(tokens: List[Dict[str, Any]], start: int, end: int) -> List[Dict[str, Any]]:
    """Tokens whose char span overlaps [start, end), in original OCR order."""
    return [t for t in tokens if t["end"] > start and t["start"] < end]


def _evidence_language(tokens: List[Dict[str, Any]], start: int, end: int,
                        default: str = "en") -> str:
    supporting = _supporting_tokens(tokens, start, end)
    langs = [t["language"] for t in supporting if t.get("language")]
    if not langs:
        return default
    return _Counter(langs).most_common(1)[0][0]


def _evidence_bbox(tokens: List[Dict[str, Any]], start: int, end: int) -> List[Any]:
    supporting = _supporting_tokens(tokens, start, end)
    return [t["bbox"] for t in supporting if t.get("bbox") is not None]


def _evidence_ocr_confidence(tokens: List[Dict[str, Any]], start: int, end: int) -> Optional[float]:
    supporting = _supporting_tokens(tokens, start, end)
    confs = [t["confidence"] for t in supporting if isinstance(t.get("confidence"), (int, float))]
    if not confs:
        return None
    return sum(confs) / len(confs)


def _finalize_extended(field_name: str, candidates: List[Candidate],
                        tokens: List[Dict[str, Any]],
                        char_map: List[int], orig_len: int,
                        high_threshold: float = 0.9) -> ExtendedFieldResult:
    """Shared selection + evidence-attachment step for the Aug 27 fields.
    Deliberately reuses `_select_best` (same deterministic ranking as MRP /
    Net Quantity) rather than inventing a new scoring system. OCR-confidence
    is a simple downgrade-only signal: low average OCR confidence can pull
    a 'high' extraction down to 'low', it never promotes anything.

    IMPORTANT: `best.start`/`best.end` are offsets into the NORMALIZED text
    (candidates were extracted from normalize_text's output), while `tokens`
    spans are offsets into the ORIGINAL joined OCR text. `char_map` (from
    _normalize_text_with_map) is used to translate the candidate's span
    back to original-text coordinates BEFORE looking up supporting tokens,
    so bbox/language/confidence are attached from the correct source
    token(s) rather than whatever happens to sit at the same numeric
    offset post-normalization."""
    best, ambiguous = _select_best(candidates)
    if best is None:
        return ExtendedFieldResult(field_name, None, "none", None, None, [],
                                    False, [c.to_dict() for c in candidates])

    orig_start, orig_end = _map_norm_span_to_original(char_map, best.start, best.end, orig_len)

    confidence = "high" if best.score >= high_threshold else "low"
    ocr_conf = _evidence_ocr_confidence(tokens, orig_start, orig_end)
    if ocr_conf is not None and ocr_conf < 0.5 and confidence == "high":
        confidence = "low"

    language = _evidence_language(tokens, orig_start, orig_end)
    bbox = _evidence_bbox(tokens, orig_start, orig_end)

    return ExtendedFieldResult(field_name, best.value, confidence, best.raw_evidence,
                                language, bbox, ambiguous,
                                [c.to_dict() for c in candidates])


def _cross_field_boundaries(text: str) -> List[int]:
    """Boundary set used ONLY by the new Aug 27 fields, so a manufacturer
    address / date / consumer-care value search stops at the start of ANY
    other recognized field label (MRP, Net Qty, or another Aug 27 field),
    not just labels of its own field. MRP's and Net Quantity's own boundary
    computation (inside extract_mrp_candidates / extract_net_qty_candidates)
    is untouched and still uses only their own field's labels+negatives."""
    all_positive = (MRP_LABELS + NET_QTY_LABELS + MANUFACTURER_LABELS +
                     MFG_DATE_LABELS + EXPIRY_LABELS + CONSUMER_CARE_LABELS)
    all_negative = MRP_NEGATIVE + NET_QTY_NEGATIVE
    return _boundaries(text, all_positive, all_negative)


# ---------------------------------------------------------------------------
# Manufacturer / Manufacturer Address
# ---------------------------------------------------------------------------

MANUFACTURER_LABELS = [
    (r"\bManufactured\s*&\s*Marketed\s*by\b", "Manufactured & Marketed by"),
    (r"\bManufactured\s*and\s*Marketed\s*by\b", "Manufactured and Marketed by"),
    (r"\bManufactured\s*by\b", "Manufactured by"),
    (r"\bManufactured\s*for\b", "Manufactured for"),
    (r"\bMarketed\s*by\b", "Marketed by"),
    (r"\bMfd\.?\s*by\b", "Mfd. by"),
    (r"\bMfg\.?\s*by\b", "Mfg. by"),
    (r"\bManufacturer\b", "Manufacturer"),
]

MANUFACTURER_WINDOW = 120  # an address block needs more room than a price/qty token
MANUFACTURER_MIN_LEN = 3   # reject near-empty / truncated-to-nothing values


def extract_manufacturer_candidates(text: str) -> List[Candidate]:
    candidates: List[Candidate] = []
    if not text:
        return candidates

    boundary_starts = _cross_field_boundaries(text)

    for label_pat, label_name in MANUFACTURER_LABELS:
        for lm in re.finditer(label_pat, text, re.IGNORECASE):
            search_start = lm.end()
            eff_end = _effective_window_end(text, lm.start(), search_start,
                                             boundary_starts, MANUFACTURER_WINDOW)
            raw_value = text[search_start:eff_end]
            value_text = raw_value.strip(" :,-\n\t")

            if len(value_text) < MANUFACTURER_MIN_LEN:
                continue  # too short / empty -> weak, false-positive risk
            if not re.search(r"[A-Za-z]", value_text):
                continue  # no letters at all -> not a plausible company/address

            # deterministic two-tier confidence: a very short fragment is
            # kept but treated as low-confidence rather than discarded
            score = 0.95 if len(value_text) >= 8 else 0.6

            candidates.append(Candidate(
                field="MANUFACTURER_ADDRESS", value=value_text,
                raw_evidence=text[lm.start():eff_end].strip(),
                label_matched=label_name, score=score,
                start=lm.start(), end=eff_end,
                reasons=[f"label='{label_name}'", f"value_len={len(value_text)}"],
                reason_codes=["MANUFACTURER_LABEL_MATCH"],
                suppressed=False,
            ))

    return candidates


def resolve_manufacturer(text: str, tokens: Optional[List[Dict[str, Any]]] = None) -> ExtendedFieldResult:
    tokens = tokens or []
    text = text or ""
    norm, char_map = _normalize_text_with_map(text)
    candidates = extract_manufacturer_candidates(norm)
    return _finalize_extended("MANUFACTURER_ADDRESS", candidates, tokens, char_map, len(text))


# ---------------------------------------------------------------------------
# Manufacturing Date
# ---------------------------------------------------------------------------

MFG_DATE_LABELS = [
    (r"\bDate\s*of\s*Manufacture\b", "Date of Manufacture"),
    (r"\bManufacturing\s*Date\b", "Manufacturing Date"),
    (r"\bManufactured\s*Date\b", "Manufactured Date"),
    (r"\bMfg\.?\s*Date\b", "Mfg Date"),
    (r"\bMfd\.?\s*Date\b", "Mfd Date"),
    (r"\bPacking\s*Date\b", "Packing Date"),
    (r"\bPacked\s*On\b", "Packed On"),
]

EXPIRY_LABELS = [
    (r"\bExpiry\s*Date\b", "Expiry Date"),
    (r"\bExpiration\s*Date\b", "Expiration Date"),
    (r"\bUse\s*By\b", "Use By"),
    (r"\bBest\s*Before\b", "Best Before"),
    (r"\bExp\.?\s*Date\b", "Exp. Date"),
    (r"\bExp\b", "Exp"),
]
MFG_DATE_NEGATIVE = [pat for pat, _name in EXPIRY_LABELS] + [r"\bBatch\b", r"\bBatch\s*No\.?\b"]

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE_DMY4_RE = re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\b")
_DATE_YMD_RE = re.compile(r"\b(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})\b")
_DATE_DMY2_RE = re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{2})\b")
_DATE_DMON_Y_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})[,\s]+(\d{2,4})\b")

MFG_DATE_WINDOW = 40


def _normalize_date(kind: str, groups) -> Optional[str]:
    """Deterministic normalization to ISO 'YYYY-MM-DD'. Returns None for
    anything not fully unambiguous — never invents missing components.
    NOTE (documented assumption): 2-digit years are read as 2000+YY, since
    packaged-goods manufacturing dates in this dataset are all modern-era;
    this is the one place a component is inferred rather than read."""
    try:
        if kind == "dmy4":
            d, mo, y = int(groups[0]), int(groups[1]), int(groups[2])
        elif kind == "dmy2":
            d, mo, yy = int(groups[0]), int(groups[1]), int(groups[2])
            y = 2000 + yy
        elif kind == "ymd":
            y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
        elif kind == "dmonY":
            d = int(groups[0])
            mon_key = groups[1][:3].lower()
            mo = _MONTH_NAMES.get(mon_key)
            if mo is None:
                return None
            y = int(groups[2])
            if y < 100:
                y += 2000
        else:
            return None
    except (ValueError, IndexError, TypeError):
        return None

    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        # real calendar validation via stdlib datetime: rejects impossible
        # dates (Feb 30, Apr 31, non-leap Feb 29, month/day out of range,
        # etc.) rather than just range-checking 1..12 / 1..31 in isolation
        return None


def _find_date_candidates_in_window(window_text: str):
    """Returns list of (start, end, kind, groups) sorted by start position,
    de-duplicating so a 4-digit-year match's first-two-digits never also
    gets picked up as a spurious separate 2-digit-year match."""
    found = []
    for m in _DATE_DMY4_RE.finditer(window_text):
        found.append((m.start(), m.end(), "dmy4", m.groups()))
    for m in _DATE_YMD_RE.finditer(window_text):
        found.append((m.start(), m.end(), "ymd", m.groups()))
    for m in _DATE_DMON_Y_RE.finditer(window_text):
        mon_key = m.group(2)[:3].lower()
        if mon_key in _MONTH_NAMES:
            found.append((m.start(), m.end(), "dmonY", m.groups()))
    for m in _DATE_DMY2_RE.finditer(window_text):
        if any(f[0] <= m.start() and m.end() <= f[1] for f in found):
            continue
        found.append((m.start(), m.end(), "dmy2", m.groups()))
    found.sort(key=lambda f: f[0])
    return found


def extract_mfg_date_candidates(text: str) -> List[Candidate]:
    candidates: List[Candidate] = []
    if not text:
        return candidates

    boundary_starts = _cross_field_boundaries(text)

    for label_pat, label_name in MFG_DATE_LABELS:
        for lm in re.finditer(label_pat, text, re.IGNORECASE):
            search_start = lm.end()
            eff_end = _effective_window_end(text, lm.start(), search_start,
                                             boundary_starts, MFG_DATE_WINDOW)
            window_text = text[search_start:eff_end]
            found = _find_date_candidates_in_window(window_text)
            if not found:
                continue

            start_off, end_off, kind, groups = found[0]  # nearest to label
            abs_start = search_start + start_off
            abs_end = search_start + end_off
            iso_value = _normalize_date(kind, groups)

            reasons = [f"label='{label_name}'", f"format='{kind}'"]
            reason_codes = ["MFG_DATE_LABEL_MATCH"]
            if kind == "dmy2":
                reasons.append("2-digit year assumed 2000+YY (documented assumption)")

            neg = _has_within_span(MFG_DATE_NEGATIVE, text, lm.start(), abs_end)
            suppressed = neg is not None
            if suppressed:
                reasons.append(f"suppressed: conflicting keyword '{neg}' within evidence span "
                                f"(likely expiry/batch date, not manufacturing date)")
                reason_codes.append("CONFLICT_SUPPRESSED")

            if iso_value is None:
                reasons.append("malformed date (out-of-range day/month) -> rejected, not guessed")
                reason_codes.append("MALFORMED_DATE_REJECTED")
                suppressed = True

            score = 0.95 if kind in ("dmy4", "ymd", "dmonY") else 0.6  # dmy2 stays lower-confidence

            candidates.append(Candidate(
                field="MANUFACTURING_DATE", value=iso_value,
                raw_evidence=text[lm.start():abs_end],
                label_matched=label_name, score=score,
                start=lm.start(), end=abs_end,
                reasons=reasons, reason_codes=reason_codes,
                suppressed=suppressed,
            ))

    return candidates


def resolve_manufacturing_date(text: str, tokens: Optional[List[Dict[str, Any]]] = None) -> ExtendedFieldResult:
    tokens = tokens or []
    text = text or ""
    norm, char_map = _normalize_text_with_map(text)
    candidates = extract_mfg_date_candidates(norm)
    return _finalize_extended("MANUFACTURING_DATE", candidates, tokens, char_map, len(text))


# ---------------------------------------------------------------------------
# Consumer Care
# ---------------------------------------------------------------------------

CONSUMER_CARE_LABELS = [
    (r"\bConsumer\s*Care\s*Details\b", "Consumer Care Details"),
    (r"\bConsumer\s*Care\s*No\.?\b", "Consumer Care No."),
    (r"\bConsumer\s*Care\s*Number\b", "Consumer Care Number"),
    (r"\bConsumer\s*Care\b", "Consumer Care"),
    (r"\bCustomer\s*Care\s*No\.?\b", "Customer Care No."),
    (r"\bCustomer\s*Care\s*Number\b", "Customer Care Number"),
    (r"\bCustomer\s*Care\b", "Customer Care"),
    (r"\bCustomer\s*Support\b", "Customer Support"),
    (r"\bContact\s*Us\b", "Contact Us"),
    (r"\bContact\s*Details\b", "Contact Details"),
]

_PHONE_RE = re.compile(
    r"(?:\+?91[-\s]?)?\b\d{10}\b"          # 9876543210 / +91 9876543210
    r"|\b\d{3,5}[-\s]\d{6,8}\b"            # 022-12345678 (STD code + number)
    r"|\b\d{2,5}(?:[-\s]\d{2,5}){1,3}\b"   # 1800-123-4567 (toll-free, multi-hyphen)
)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

CONSUMER_CARE_WINDOW = 60


def extract_consumer_care_candidates(text: str) -> List[Candidate]:
    candidates: List[Candidate] = []
    if not text:
        return candidates

    boundary_starts = _cross_field_boundaries(text)

    for label_pat, label_name in CONSUMER_CARE_LABELS:
        for lm in re.finditer(label_pat, text, re.IGNORECASE):
            search_start = lm.end()
            eff_end = _effective_window_end(text, lm.start(), search_start,
                                             boundary_starts, CONSUMER_CARE_WINDOW)
            window_text = text[search_start:eff_end]

            phone_m = _PHONE_RE.search(window_text)
            email_m = _EMAIL_RE.search(window_text)
            phone = phone_m.group(0) if phone_m else None
            email = email_m.group(0) if email_m else None

            if phone is None and email is None:
                continue  # no meaningful contact info found -> skip, not a candidate

            last_end = max(
                (m.end() for m in (phone_m, email_m) if m is not None),
                default=0,
            )
            abs_end = search_start + last_end

            reasons = [f"label='{label_name}'"]
            reason_codes = ["CONSUMER_CARE_LABEL_MATCH"]
            if phone:
                reason_codes.append("PHONE_MATCH")
            if email:
                reason_codes.append("EMAIL_MATCH")

            candidates.append(Candidate(
                field="CONSUMER_CARE", value={"phone": phone, "email": email},
                raw_evidence=text[lm.start():abs_end],
                label_matched=label_name, score=0.9,
                start=lm.start(), end=abs_end,
                reasons=reasons, reason_codes=reason_codes,
                suppressed=False,
            ))

    return candidates


def resolve_consumer_care(text: str, tokens: Optional[List[Dict[str, Any]]] = None) -> ExtendedFieldResult:
    tokens = tokens or []
    text = text or ""
    norm, char_map = _normalize_text_with_map(text)
    candidates = extract_consumer_care_candidates(norm)
    return _finalize_extended("CONSUMER_CARE", candidates, tokens, char_map, len(text))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _coerce_to_text(ocr_input: Union[str, List[Dict[str, Any]], None]) -> str:
    """Accept either a raw string or a list of OCR token dicts
    (forward-compatible with the eventual OCR Engineer schema:
    {'text':..., 'bbox':..., 'confidence':..., 'language':...}).
    Bounding box / confidence are not used yet — text-window matching
    only, per Aug 26 scope. bbox-aware overlay mapping is Aug 27+ work."""
    if isinstance(ocr_input, str):
        return ocr_input
    if isinstance(ocr_input, list):
        return " ".join(
            str(tok.get("text", "")) for tok in ocr_input
            if isinstance(tok, dict) and tok.get("text") is not None
        )
    return ""


def map_fields(ocr_input: Union[str, List[Dict[str, Any]], None]) -> Dict[str, Any]:
    """Main integration entry point.
    Input: raw OCR string, OR list of OCR token dicts — plain {'text':...}
    tokens (Aug 26 compatibility) or full EasyOCR-style tokens with
    'bbox'/'confidence'/'language' (Aug 27). Missing optional metadata on
    any token is tolerated, never crashes.
    Output: dict with 'MRP', 'NET_QUANTITY', 'MANUFACTURER_ADDRESS',
    'MANUFACTURING_DATE', and 'CONSUMER_CARE' result dicts.
    """
    text, tokens = _build_token_index(ocr_input)

    mrp = resolve_mrp(text)
    net_qty = resolve_net_quantity(text)
    manufacturer = resolve_manufacturer(text, tokens)
    mfg_date = resolve_manufacturing_date(text, tokens)
    consumer_care = resolve_consumer_care(text, tokens)

    return {
        "MRP": mrp.to_dict(),
        "NET_QUANTITY": net_qty.to_dict(),
        "MANUFACTURER_ADDRESS": manufacturer.to_dict(),
        "MANUFACTURING_DATE": mfg_date.to_dict(),
        "CONSUMER_CARE": consumer_care.to_dict(),
    }


if __name__ == "__main__":
    sample = ("MRP ₹249 (incl. of all taxes) Net Qty. 500 g "
              "Manufactured by ABC Foods Pvt Ltd, Mumbai "
              "Mfg Date 01/06/2026 Consumer Care 1800-123-4567")
    import json
    print(json.dumps(map_fields(sample), indent=2))