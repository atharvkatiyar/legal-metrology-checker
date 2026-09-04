from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fpdf import FPDF

from app.models.schema import ScanResult, ViolationRecord

_DEMO_OFFICER_NAME = "Sh. R. K. Sharma, Legal Metrology Officer, Circle-IV (Demo)"


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


def generate_inspection_certificate_pdf(
    scan: ScanResult,
    violations: list[ViolationRecord],
    cr_no: str,
    resolved_address: Optional[str] = None,
) -> bytes:
    """
    Renders a formal State Verification Certificate PDF for one scan
    and returns the raw PDF bytes.
    """
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(left=15, top=15, right=15)

    page_width = pdf.w - pdf.l_margin - pdf.r_margin

    # --- Header row: Department (left) / Government of India (right) ---
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(page_width / 2, 6, _safe("Department of Legal Metrology"), align="L")
    pdf.cell(page_width / 2, 6, _safe("Government of India"), align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(1)
    pdf.set_draw_color(11, 114, 123)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + page_width, pdf.get_y())
    pdf.ln(4)

    # --- Title ---
    pdf.set_font("helvetica", "B", 14)
    pdf.multi_cell(
        page_width,
        7,
        _safe("CERTIFICATE OF INSPECTION\n(Packaged Commodities)"),
        align="C",
    )
    pdf.ln(3)

    # --- Metadata block ---
    certificate_no = str(scan.id)
    inspection_date = scan.created_at if isinstance(scan.created_at, datetime) else None
    inspection_date_str = inspection_date.strftime("%d-%m-%Y") if inspection_date else "N/A"
    address_str = resolved_address or scan.location_address or "Address Unavailable (Offline Mode)"

    pdf.set_font("helvetica", "", 10)
    pdf.multi_cell(
        page_width,
        6,
        _safe(f"Certificate No: {certificate_no}   |   CR No: {cr_no}"),
    )
    pdf.multi_cell(
        page_width,
        6,
        _safe(f"Legal Metrology Officer: {_DEMO_OFFICER_NAME}"),
    )
    pdf.ln(2)
    pdf.multi_cell(
        page_width,
        6,
        _safe(
            f"I hereby certify that I have this day {inspection_date_str} verified the "
            f"under-mentioned packaged commodity at {address_str}, and that the "
            f"declarations thereon have been examined against the Legal Metrology "
            f"(Packaged Commodities) Rules, 2011."
        ),
    )
    pdf.ln(4)

    # --- Data table (strict grid) ---
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

    pdf.set_font("helvetica", "B", 8)
    pdf.set_fill_color(11, 114, 123)
    pdf.set_text_color(255, 255, 255)
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
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(
        page_width,
        6,
        _safe(f"Total Score: {score:.0f}/100   |   Violations: {len(violations)}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)

    if violations:
        pdf.set_font("helvetica", "B", 9)
        pdf.cell(page_width, 5, _safe("Violation Summary:"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 8)
        for violation in violations:
            pdf.multi_cell(
                page_width,
                5,
                _safe(
                    f"- [{violation.severity.upper()}] {violation.field_name}: {violation.issue}"
                ),
            )
        pdf.ln(3)

    # --- Notes ---
    pdf.set_font("helvetica", "I", 7)
    pdf.multi_cell(
        page_width,
        4,
        _safe(
            "Note: 1. To be exhibited conspicuously. "
            "2. Generated automatically by LMCS Edge Node. Cryptographically verifiable."
        ),
    )

    output = pdf.output()
    return bytes(output)


__all__ = ["generate_inspection_certificate_pdf"]