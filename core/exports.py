import csv
import io

from django.http import HttpResponse
from django.core.exceptions import ValidationError
from docx import Document as WordDocument
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from xml.sax.saxutils import escape

COLUMNS = ["reference", "date", "kind", "party", "total", "paid", "balance"]


def safe(value):
    text = str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def export(rows, format, title, company, columns=None, filename="kofad-transactions", sheet_name="Transactions"):
    columns = columns or [(c,c.title()) for c in COLUMNS]
    headers = [title for key,title in columns]
    raw = [[r[key] for key,title in columns] for r in rows]
    data = [[str(value) for value in row] for row in raw]
    output = io.BytesIO()
    if format == "xlsx":
        book = Workbook()
        sheet = book.active
        sheet.title = (sheet_name or "Export")[:31]
        sheet.append([company.name, title])
        sheet.append(headers)
        from decimal import Decimal
        for row in raw:
            sheet.append([float(v) if isinstance(v,Decimal) else v if isinstance(v,(int,float)) else safe(v) for v in row])
        sheet.freeze_panes = "A3"
        from openpyxl.utils import get_column_letter
        sheet.auto_filter.ref = f"A2:{get_column_letter(len(headers))}{max(2, sheet.max_row)}"
        for col in [get_column_letter(i) for i in range(1,len(headers)+1)]:
            sheet.column_dimensions[col].width = 25 if col in "ABD" else 18
        book.save(output)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif format == "docx":
        from docx.shared import Inches
        from docx.enum.section import WD_ORIENT
        doc = WordDocument()
        section = doc.sections[0]
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Inches(11.7)
        section.page_height = Inches(8.3)
        section.left_margin = section.right_margin = Inches(.5)
        doc.add_heading(company.name, 0)
        doc.add_heading(title, 1)
        doc.add_paragraph(f"Currency: {company.currency}. Balances reflect allocations recorded at export time.")
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Light Shading Accent 1"
        for cell, header in zip(table.rows[0].cells, headers):
            cell.text = header
        for row in data:
            for cell, value in zip(table.add_row().cells, row):
                cell.text = value
        doc.save(output)
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif format == "pdf":
        styles = getSampleStyleSheet()
        style = styles["BodyText"]
        style.fontSize = 7
        style.leading = 10
        cells = [[Paragraph(escape(v), style) for v in row] for row in [headers] + data]
        table = Table(cells, repeatRows=1, colWidths=[790 / len(headers)] * len(headers))
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7ede8")),
            ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#d9ded8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=24, rightMargin=24).build([
            Paragraph(escape(company.name), styles["Title"]), Paragraph(escape(title), styles["Heading2"]),
            Spacer(1, 12), table])
        content_type = "application/pdf"
    elif format == "csv":
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow(headers)
        writer.writerows([[safe(v) for v in row] for row in data])
        output.write(text.getvalue().encode("utf-8-sig"))
        content_type = "text/csv"
    else:
        raise ValidationError("Unsupported export format.")
    response = HttpResponse(output.getvalue(), content_type=content_type)
    safe_filename = "".join(ch for ch in filename.lower() if ch.isalnum() or ch in "-_") or "kofad-export"
    response["Content-Disposition"] = f'attachment; filename="{safe_filename}.{format}"'
    return response
