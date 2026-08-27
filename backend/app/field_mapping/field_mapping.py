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


# ---------------------------------------------------------------------------
# Net Quantity config
# ---------------------------------------------------------------------------

NET_QTY_LABELS = [
    (r"\bNet\s*Quantity\b", "Net Quantity"),
    (r"\bNet\s*Qty\b", "Net Qty"),
    (r"\bNet\s*Weight\b", "Net Weight"),
    (r"\bNet\s*Wt\b", "Net Wt"),
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
    Input: raw OCR string, OR list of OCR token dicts (see _coerce_to_text).
    Output: dict with 'MRP' and 'NET_QUANTITY' FieldResult dicts.
    """
    text = _coerce_to_text(ocr_input)
    mrp = resolve_mrp(text)
    net_qty = resolve_net_quantity(text)
    return {
        "MRP": mrp.to_dict(),
        "NET_QUANTITY": net_qty.to_dict(),
    }


if __name__ == "__main__":
    sample = "MRP ₹249 (incl. of all taxes) Net Qty. 500 g Offer Price ₹199"
    import json
    print(json.dumps(map_fields(sample), indent=2))
