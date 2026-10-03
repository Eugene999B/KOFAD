import io
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Company
from .services import balance


INK = colors.HexColor("#102b46")
TEAL = colors.HexColor("#138895")
MUTED = colors.HexColor("#657585")
LINE = colors.HexColor("#cfd8df")
SOFT = colors.HexColor("#f4f7f9")


def _text(value):
    return escape(str(value or ""))


def _mode(value):
    return {
        "retail_pack": "Retail pack",
        "retail_unit": "Retail",
        "wholesale_pack": "Wholesale pack",
        "wholesale_unit": "Wholesale",
        "writeoff": "Write-off",
    }.get(value, str(value).replace("_", " ").title())


def _business_contacts(company):
    return " / ".join(
        value.strip() for value in (company.phone, company.secondary_phone)
        if value and value.strip()
    ) or "Not configured"


def _location(document, company):
    return document.branch.address.strip() if document.branch.address else (
        company.address.strip() if company.address else document.branch.name
    )


def _logo(max_width, max_height):
    path = Path(settings.BASE_DIR) / "static" / "brand" / "kofad-emblem.png"
    if not path.exists():
        return None
    image = Image(str(path))
    ratio = min(max_width / image.imageWidth, max_height / image.imageHeight)
    image.drawWidth = image.imageWidth * ratio
    image.drawHeight = image.imageHeight * ratio
    return image


def _styles(thermal=False, narrow=False):
    base = getSampleStyleSheet()
    body_size = 7.0 if thermal else 8.5
    if narrow:
        body_size = 6.3
    body = ParagraphStyle(
        "ReceiptBody", parent=base["BodyText"], fontName="Helvetica",
        fontSize=body_size, leading=body_size * 1.35, textColor=INK,
    )
    small = ParagraphStyle(
        "ReceiptSmall", parent=body, fontSize=max(5.4, body_size - 1.2),
        leading=max(6.5, body_size * 1.15), textColor=MUTED,
    )
    right = ParagraphStyle("ReceiptRight", parent=body, alignment=TA_RIGHT)
    center = ParagraphStyle("ReceiptCenter", parent=small, alignment=TA_CENTER)
    return body, small, right, center


def _money(company, value):
    return f"{company.currency} {value}"


def _thermal_height(document, width):
    lines = max(1, document.lines.count())
    payments = max(1, document.payments.count())
    width_factor = 1.28 if width <= 58 * mm else 1.0
    estimated_mm = 112 + lines * 13 * width_factor + payments * 8 + (16 if document.note else 0)
    return max(160 * mm, min(900 * mm, estimated_mm * mm))


def render_receipt_pdf(document, format_name="a4"):
    company = Company.objects.first() or Company()
    thermal = format_name in {"thermal80", "thermal58"}
    narrow = format_name == "thermal58"

    if format_name == "a4":
        page_size = A4
        left = right = 14 * mm
        top = bottom = 12 * mm
        page_width = A4[0] - left - right
    elif format_name == "thermal80":
        physical_width = 80 * mm
        page_size = (physical_width, _thermal_height(document, physical_width))
        left = right = 3 * mm
        top = bottom = 4 * mm
        page_width = physical_width - left - right
    elif format_name == "thermal58":
        physical_width = 58 * mm
        page_size = (physical_width, _thermal_height(document, physical_width))
        left = right = 2 * mm
        top = bottom = 3 * mm
        page_width = physical_width - left - right
    else:
        raise ValueError("Unsupported receipt PDF format.")

    output = io.BytesIO()
    pdf = SimpleDocTemplate(
        output, pagesize=page_size, leftMargin=left, rightMargin=right,
        topMargin=top, bottomMargin=bottom, title=document.reference,
        author=company.name,
    )
    body, small, right_style, center = _styles(thermal, narrow)
    story = []

    logo = _logo(15 * mm if not thermal else 10 * mm, 15 * mm if not thermal else 10 * mm)
    company_name = Paragraph(f"<b>{_text(company.name)}</b><br/><font color='#657585'>{_text(_business_contacts(company))}</font>", body)
    receipt_title = Paragraph(
        f"<font color='#138895'><b>{_text(document.get_kind_display().upper())}</b></font><br/>"
        f"<b>{_text(document.reference)}</b><br/>"
        f"<font color='#657585'>{_text(timezone.localtime(document.created_at).strftime('%d %B %Y, %H:%M'))}</font>",
        right_style if not thermal else center,
    )
    if thermal:
        header_items = []
        if logo:
            header_items.append([logo])
        header_items.extend([[company_name], [receipt_title]])
        header = Table(header_items, colWidths=[page_width])
        header.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
    else:
        brand = [logo, company_name] if logo else [company_name]
        brand_table = Table([brand], colWidths=([18 * mm, page_width * .42] if logo else [page_width * .48]))
        brand_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        header = Table([[brand_table, receipt_title]], colWidths=[page_width * .62, page_width * .38])
        header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))

    story.append(header)
    story.append(Spacer(1, 3 * mm))
    story.append(Table([[""]], colWidths=[page_width], rowHeights=[.55 * mm],
                       style=[("BACKGROUND", (0, 0), (-1, -1), TEAL)]))
    story.append(Spacer(1, 3 * mm))

    party_label = "ACCOUNT"
    party_name = "Internal record"
    party_phone = ""
    if document.party:
        party_label = "SUPPLIER / CREDITOR" if document.kind in ("purchase", "creditor_charge", "supplier_return", "supplier_payment") else "CUSTOMER"
        party_name = document.party.name
        if company.receipt_show_contact_phone:
            party_phone = document.party.phone
    elif document.kind == "inventory_writeoff":
        party_name = "Internal inventory record"

    location_text = f"<b>{_text(document.branch.name)}</b><br/><font color='#657585'>{_text(_location(document, company))}</font>"
    party_text = f"<b>{_text(party_name)}</b>"
    if party_phone:
        party_text += f"<br/><font color='#657585'>{_text(party_phone)}</font>"
    meta = [
        [Paragraph("<font color='#657585'>LOCATION</font>", small), Paragraph(f"<font color='#657585'>{party_label}</font>", small)],
        [Paragraph(location_text, body), Paragraph(party_text, body)],
    ]
    if company.receipt_show_staff and not thermal:
        staff = document.created_by.get_full_name() or document.created_by.username
        meta[0].append(Paragraph("<font color='#657585'>RECORDED BY</font>", small))
        meta[1].append(Paragraph(f"<b>{_text(staff)}</b>", body))
    meta_cols = 2 if len(meta[0]) == 2 else 3
    meta_table = Table(meta, colWidths=[page_width / meta_cols] * meta_cols)
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
    ]))
    story.append(meta_table)
    if company.receipt_show_staff and thermal:
        staff = document.created_by.get_full_name() or document.created_by.username
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(f"<font color='#657585'>Recorded by</font> <b>{_text(staff)}</b>", small))
    if document.document_date:
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(f"<font color='#657585'>Business / invoice date</font> <b>{_text(document.document_date.strftime('%d %b %Y'))}</b>", body))
    if document.external_reference:
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(f"<font color='#657585'>Supplier reference</font> <b>{_text(document.external_reference)}</b>", body))
    if document.due_date:
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(f"<font color='#657585'>Due date</font> <b>{_text(document.due_date.strftime('%d %b %Y'))}</b>", body))
    story.append(Spacer(1, 3 * mm))

    line_rows = []
    if thermal:
        line_rows.append([
            Paragraph("<b>ITEM</b>", small),
            Paragraph("<b>QTY</b>", small),
            Paragraph("<b>TOTAL</b>", right_style),
        ])
        for line in document.lines.all():
            item = f"<b>{_text(line.description)}</b><br/><font color='#657585'>{_text(_mode(line.mode))} @ {line.unit_price}</font>"
            qty = f"{line.quantity}" + (f" x {line.factor}" if line.factor > 1 else "")
            line_rows.append([
                Paragraph(item, small),
                Paragraph(_text(qty), small),
                Paragraph(_text(line.total), right_style),
            ])
        if not document.lines.exists():
            line_rows.append([Paragraph(_text(document.note or document.get_kind_display()), small), "", ""])
        widths = [page_width * .58, page_width * .16, page_width * .26]
    else:
        line_rows.append([
            Paragraph("<b>ITEM</b>", small), Paragraph("<b>MODE</b>", small),
            Paragraph("<b>QTY</b>", small), Paragraph("<b>PRICE</b>", right_style),
            Paragraph("<b>TOTAL</b>", right_style),
        ])
        for line in document.lines.all():
            qty = f"{line.quantity}" + (f" x {line.factor}" if line.factor > 1 else "")
            line_rows.append([
                Paragraph(f"<b>{_text(line.description)}</b>", body),
                Paragraph(_text(_mode(line.mode)), small),
                Paragraph(_text(qty), body),
                Paragraph(_text(line.unit_price), right_style),
                Paragraph(_text(line.total), right_style),
            ])
        if not document.lines.exists():
            line_rows.append([Paragraph(_text(document.note or document.get_kind_display()), body), "", "", "", ""])
        widths = [page_width * .36, page_width * .18, page_width * .12, page_width * .15, page_width * .19]

    line_table = Table(line_rows, colWidths=widths, repeatRows=1)
    line_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("LINEBELOW", (0, 0), (-1, 0), .8, INK),
        ("LINEBELOW", (0, 1), (-1, -1), .25, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2 if thermal else 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2 if thermal else 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4 if thermal else 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4 if thermal else 6),
    ]))
    story.append(line_table)
    story.append(Spacer(1, 4 * mm))

    if document.kind == "supplier_payment" and document.allocations.exists():
        story.append(Paragraph("<b>APPLIED TO</b>", small))
        story.append(Spacer(1, 1.5 * mm))
        allocation_rows = [[
            Paragraph("<b>BILL</b>", small),
            Paragraph("<b>SUPPLIER REF.</b>", small),
            Paragraph("<b>AMOUNT</b>", right_style),
        ]]
        for allocation in document.allocations.select_related("invoice").all():
            invoice = allocation.invoice
            allocation_rows.append([
                Paragraph(_text(invoice.reference), small),
                Paragraph(_text(invoice.external_reference or "—"), small),
                Paragraph(_money(company, allocation.amount), right_style),
            ])
        allocation_table = Table(
            allocation_rows,
            colWidths=[page_width * .38, page_width * .34, page_width * .28],
            repeatRows=1,
        )
        allocation_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), SOFT),
            ("LINEBELOW", (0, 0), (-1, -1), .25, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2 if thermal else 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2 if thermal else 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(allocation_table)
        story.append(Spacer(1, 4 * mm))

    outstanding = balance(document) if document.kind in ("sale", "purchase", "creditor_charge") else None
    totals = []
    if document.kind == "inventory_writeoff":
        totals.append(["Inventory loss value", _money(company, document.total)])
    elif document.kind == "creditor_charge":
        totals.append(["BILL TOTAL", _money(company, document.total)])
    elif document.kind == "supplier_payment":
        totals.append(["PAYMENT TOTAL", _money(company, document.total)])
    else:
        totals.append(["TOTAL", _money(company, document.total)])
        totals.append(["Paid / refunded at posting", _money(company, document.paid)])
    if outstanding is not None:
        totals.append(["Current outstanding", _money(company, outstanding)])
    for payment in document.payments.all():
        label = payment.get_method_display()
        if company.receipt_show_payment_reference and payment.reference:
            label += f" - {payment.reference}"
        totals.append([label, _money(company, payment.amount)])

    total_rows = [[Paragraph(_text(label), small), Paragraph(f"<b>{_text(value)}</b>", right_style)] for label, value in totals]
    totals_table = Table(total_rows, colWidths=[page_width * .6, page_width * .4])
    totals_table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), .25, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(totals_table)

    if document.note:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(f"<b>Note:</b> {_text(document.note)}", small))

    story.append(Spacer(1, 5 * mm))
    footer = (
        f"<b>{_text(company.receipt_footer)}</b><br/>"
        f"{_text(company.name)} - {_text(_business_contacts(company))}<br/>"
        f"<font color='#657585'>Original transaction reference: {_text(document.reference)}</font>"
    )
    story.append(Paragraph(footer, center))
    pdf.build(story)

    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{document.reference}-{format_name}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response
