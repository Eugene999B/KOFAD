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


def export(rows, format, title, company):
    headers = [c.title() for c in COLUMNS]
    data = [[str(r[c]) for c in COLUMNS] for r in rows]
    output = io.BytesIO()
    if format == "xlsx":
        book = Workbook()
        sheet = book.active
        sheet.title = "Transactions"
        sheet.append([company.name, title])
        sheet.append(headers)
        for row in data:
            sheet.append([safe(v) for v in row])
        sheet.freeze_panes = "A3"
        sheet.auto_filter.ref = f"A2:G{max(2, sheet.max_row)}"
        for col in "ABCDEFG":
            sheet.column_dimensions[col].width = 25 if col in "ABD" else 18
        book.save(output)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif format == "docx":
        doc = WordDocument()
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
        table = Table(cells, repeatRows=1, colWidths=[150, 85, 80, 130, 80, 80, 80])
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
    response["Content-Disposition"] = f'attachment; filename="kofad-transactions.{format}"'
    return response
