# app/services/pdf_generator.py
from __future__ import annotations
import os
import uuid
from datetime import datetime
from typing import Any, Optional
from fpdf import FPDF
from app.models.schema import ScanResult, ViolationRecord

_DEMO_OFFICER_NAME = "Sh. R. K. Sharma, Legal Metrology Officer, Circle-IV (Demo)"

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
_CCPA_LOGO_PATH = os.path.join(_ASSETS_DIR, "ccpa_logo.jpg")
_CONSUMER_AFFAIRS_LOGO_PATH = os.path.join(_ASSETS_DIR, "consumer_affairs_logo.jpg")
_GOV_EMBLEM_PATH = os.path.join(_ASSETS_DIR, "gov_emblem.jpg")


def _safe(text: Any) -> str:
    """
    fpdf2's core fonts (helvetica) only support latin-1. OCR-derived
    text may contain characters outside that range; replace rather
    than crash the PDF pipeline.
    """
    if text is None:
        return "N/A"
    return str(text).encode("latin-1", "replace").decode("latin-1")


def _extract_field_value(extracted_fields: Optional[dict], key: str) -> Any:
    if not isinstance(extracted_fields, dict):
        return None
    field = extracted_fields.get(key)
    if not isinstance(field, dict):
        return None
    return field.get("value")


def _format_mrp(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"Rs. {float(value):.2f}"
    except (TypeError, ValueError):
        return _safe(value)


def _format_net_quantity(value: Any) -> str:
    if not isinstance(value, dict):
        return "N/A"
    amount = value.get("amount")
    unit = value.get("unit")
    if amount is None or unit is None:
        return "N/A"
    try:
        return f"{float(amount):g} {unit}"
    except (TypeError, ValueError):
        return _safe(f"{amount} {unit}")


def _format_mfg_date(value: Any) -> str:
    if value is None:
        return "N/A"
    return _safe(value)


def _format_manufacturer(value: Any, max_len: int = 60) -> str:
    if value is None:
        return "N/A"
    text = str(value)
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return _safe(text)


def _commodity_description(scan: ScanResult) -> str:
    if scan.product is not None and scan.product.name:
        return _safe(scan.product.name)
    return "Packaged Commodity (As Inspected)"


def _safe_image(pdf: FPDF, path: str, x: float, y: float, w: float, h: float = 0) -> None:
    """
    Places an image at the given position, silently skipping (rather than
    crashing the whole PDF pipeline) when the asset file is missing.
    """
    try:
        pdf.image(path, x=x, y=y, w=w, h=h)
    except FileNotFoundError:
        pass
    except RuntimeError:
        pass


def generate_inspection_certificate_pdf(
    scan: ScanResult,
    violations: list[ViolationRecord],
    cr_no: str,
    resolved_address: Optional[str] = None,
    officer_name: Optional[str] = None,
) -> bytes:
    """
    Renders a formal, strictly black-and-white State Verification
    Certificate PDF for one scan and returns the raw PDF bytes.
    """
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_margins(left=15, top=25, right=15)
    
    # --- Strict color enforcement: everything black, no color fills ---
    pdf.set_text_color(0, 0, 0)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_fill_color(255, 255, 255)
    
    # --- Continuous page border ---
    pdf.set_line_width(0.4)
    pdf.rect(5, 5, 200, 287)
    
    page_width = pdf.w - pdf.l_margin - pdf.r_margin
    
    # --- Header logos ---
    _safe_image(pdf, _CCPA_LOGO_PATH, x=10, y=8, w=25)
    _safe_image(pdf, _CONSUMER_AFFAIRS_LOGO_PATH, x=160, y=8, w=40)
    
    # --- Header text (restricted between logos: X=35 to X=160) ---
    pdf.set_xy(35, 9)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("helvetica", "B", 13)
    pdf.multi_cell(125, 6, _safe("Department of Legal Metrology"), align="C")
    
    pdf.set_xy(35, pdf.get_y())
    pdf.set_font("helvetica", "B", 11)
    pdf.multi_cell(
        125,
        6,
        _safe("CERTIFICATE OF INSPECTION\n(Packaged Commodities)"),
        align="C",
    )
    
    pdf.set_y(max(pdf.get_y(), 34))
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + page_width, pdf.get_y())
    pdf.ln(4)
    
    # --- Central watermark (faint, behind content) ---
    with pdf.local_context(fill_opacity=0.1):
        _safe_image(pdf, _GOV_EMBLEM_PATH, x=55, y=100, w=100)
        
    pdf.set_xy(pdf.l_margin, pdf.get_y())
    
    # --- Metadata block ---
    certificate_no = f"CERT-{str(scan.id)[:8].upper()}"
    inspection_date = scan.created_at if isinstance(scan.created_at, datetime) else None
    inspection_date_str = inspection_date.strftime("%d-%m-%Y") if inspection_date else "N/A"
    address_str = resolved_address or getattr(scan, "location_address", None) or "Address Unavailable (Offline Mode)"
    
    officer_display_name = officer_name if officer_name else _DEMO_OFFICER_NAME
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("helvetica", "", 10)
    pdf.multi_cell(
        0,
        6,
        _safe(f"Certificate No: {certificate_no}   |   CR No: {cr_no}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.multi_cell(
        0,
        6,
        _safe(f"Legal Metrology Officer: {officer_display_name}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(2)
    pdf.multi_cell(
        0,
        6,
        _safe(
            f"I hereby certify that I have this day {inspection_date_str} verified the "
            f"under-mentioned packaged commodity at {address_str}, and that the "
            f"declarations thereon have been examined against the Legal Metrology "
            f"(Packaged Commodities) Rules, 2011."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)
    
    # --- Data table ---
    extracted_fields = scan.extracted_fields or {}
    mrp_value = _format_mrp(_extract_field_value(extracted_fields, "MRP"))
    net_qty_value = _format_net_quantity(_extract_field_value(extracted_fields, "NET_QUANTITY"))
    manufacturer_value = _format_manufacturer(_extract_field_value(extracted_fields, "MANUFACTURER_ADDRESS"))
    mfg_date_value = _format_mfg_date(_extract_field_value(extracted_fields, "MANUFACTURING_DATE"))
    commodity_desc = _commodity_description(scan)
    row_status = "PASS" if scan.is_compliant else "FAIL"
    
    headers = ["Description", "Make", "Net Quantity", "MRP", "Mfg Date", "Status"]
    col_widths = [
        page_width * 0.22,
        page_width * 0.24,
        page_width * 0.14,
        page_width * 0.14,
        page_width * 0.14,
        page_width * 0.12,
    ]
    
    pdf.set_draw_color(0, 0, 0)
    pdf.set_fill_color(220, 220, 220)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("helvetica", "B", 8)
    for header, width in zip(headers, col_widths):
        pdf.cell(width, 8, _safe(header), border=1, align="C", fill=True)
    pdf.ln(8)
    
    pdf.set_font("helvetica", "", 8)
    pdf.set_text_color(0, 0, 0)
    row_values = [
        commodity_desc,
        manufacturer_value,
        net_qty_value,
        mrp_value,
        mfg_date_value,
        row_status,
    ]
    row_height = 10
    start_y = pdf.get_y()
    start_x = pdf.get_x()
    for value, width in zip(row_values, col_widths):
        cell_x = pdf.get_x()
        cell_y = pdf.get_y()
        pdf.multi_cell(width, row_height / 2, _safe(value), border=1, align="C")
        pdf.set_xy(cell_x + width, cell_y)
    pdf.set_xy(start_x, start_y + row_height)
    pdf.ln(2)
    
    # --- Footer: score / violations ---
    pdf.ln(4)
    score = scan.compliance_score if scan.compliance_score is not None else 0.0
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(
        0,
        6,
        _safe(f"Final Score: {score:.0f}/100 | Total Violations Found: {len(violations)}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)
    
    if violations:
        pdf.set_font("helvetica", "B", 9)
        pdf.cell(0, 5, _safe("Violation Summary:"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 8)
        for violation in violations:
            pdf.multi_cell(
                0,
                5,
                _safe(
                    f"- [{violation.severity.upper()}] {violation.field_name}: {violation.issue}"
                ),
                new_x="LMARGIN",
                new_y="NEXT",
            )
        pdf.ln(3)
        
    # --- Legal disclaimers ---
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("helvetica", "I", 7)
    pdf.multi_cell(
        0,
        4,
        _safe(
            "Note: 1. This certificate shall be exhibited in a conspicuous place on the "
            "premises where the commodity is sold.\n"
            "2. Generated automatically by LMCS Edge Node. Cryptographically verifiable."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(8)
    
    # --- Bottom block: generation timestamp (left) / signature (right) ---
    generated_on = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    timestamp_str = datetime.now().strftime("%Y.%m.%d %H:%M:%S +05:30")
    
    current_y = pdf.get_y()
    
    pdf.set_font("helvetica", "", 8)
    pdf.set_xy(pdf.l_margin, current_y)
    pdf.cell(page_width / 2, 5, _safe(f"Generated On: {generated_on}"), align="L")
    
    sig_box_x = pdf.l_margin + page_width - 85
    sig_box_y = current_y
    
    signatory_name = officer_name.upper() if officer_name else "R. K. SHARMA"
    
    pdf.set_xy(sig_box_x, sig_box_y)
    pdf.set_font("helvetica", "B", 8)
    pdf.cell(85, 4, _safe(f"Digitally signed by {signatory_name}"), align="R", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_x(sig_box_x)
    pdf.set_font("helvetica", "", 7)
    pdf.cell(85, 4, _safe("Reason: Approved Inspection Certificate"), align="R", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_x(sig_box_x)
    pdf.cell(85, 4, _safe(f"Date: {timestamp_str}"), align="R", new_x="LMARGIN", new_y="NEXT")

    output = pdf.output()
    return bytes(output)

__all__ = ["generate_inspection_certificate_pdf"]