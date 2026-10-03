import csv
import io
from datetime import date, datetime
from decimal import Decimal
from xml.sax.saxutils import escape

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from docx import Document as WordDocument
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape, portrait
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

COLUMNS = ["reference", "date", "kind", "party", "total", "paid", "balance"]
CHARCOAL, COPPER, CREAM, LIGHT, WHITE = "171717", "B87333", "F6F2EA", "ECE8E1", "FFFFFF"


def safe(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def display(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


def excel_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float, date, datetime)):
        return value
    return safe(value)


def shade_word(cell, colour):
    tc_pr = cell._tc.get_or_add_tcPr()
    node = tc_pr.find(qn("w:shd"))
    if node is None:
        node = OxmlElement("w:shd")
        tc_pr.append(node)
    node.set(qn("w:fill"), colour)


def export(
    rows, format, title, company, columns=None, filename="kofad-transactions",
    sheet_name="Transactions", metadata=None, summary=None, notes=None,
):
    columns = columns or [(key, key.title()) for key in COLUMNS]
    headers = [label for _, label in columns]
    raw = [[row.get(key, "") for key, _ in columns] for row in rows]
    shown = [[display(value) for value in row] for row in raw]
    metadata = [(str(k), display(v)) for k, v in (metadata or {}).items() if v not in (None, "")]
    summary = [(str(k), v) for k, v in (summary or {}).items()]
    notes = [str(item) for item in (notes or []) if item]
    output = io.BytesIO()

    if format == "xlsx":
        book = Workbook()
        sheet = book.active
        sheet.title = (sheet_name or "Export")[:31]
        book.properties.title = title
        book.properties.creator = company.name
        width = max(1, len(headers))
        row_no = 1

        sheet.merge_cells(start_row=row_no, start_column=1, end_row=row_no, end_column=width)
        cell = sheet.cell(row_no, 1, company.name)
        cell.font = Font(size=18, bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=CHARCOAL)
        cell.alignment = Alignment(vertical="center")
        sheet.row_dimensions[row_no].height = 32
        row_no += 1

        sheet.merge_cells(start_row=row_no, start_column=1, end_row=row_no, end_column=width)
        cell = sheet.cell(row_no, 1, title)
        cell.font = Font(size=12, bold=True, color=CHARCOAL)
        cell.fill = PatternFill("solid", fgColor=CREAM)
        sheet.row_dimensions[row_no].height = 24
        row_no += 1

        for key, value in metadata:
            sheet.cell(row_no, 1, key).font = Font(bold=True, color="6B6B6B")
            if width > 1:
                sheet.merge_cells(start_row=row_no, start_column=2, end_row=row_no, end_column=width)
                sheet.cell(row_no, 2, value)
            row_no += 1

        if summary:
            row_no += 1
            card_cols = min(4, width)
            start = row_no
            for index, (key, value) in enumerate(summary):
                col = index % card_cols + 1
                card_row = start + (index // card_cols) * 2
                sheet.cell(card_row, col, key)
                sheet.cell(card_row + 1, col, excel_value(value))
                sheet.cell(card_row, col).fill = PatternFill("solid", fgColor=LIGHT)
                sheet.cell(card_row, col).font = Font(size=9, bold=True, color="6B6B6B")
                sheet.cell(card_row + 1, col).font = Font(size=12, bold=True, color=CHARCOAL)
                if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
                    sheet.cell(card_row + 1, col).number_format = '#,##0.00'
            row_no = start + ((len(summary) - 1) // card_cols + 1) * 2 + 1

        header_row = row_no
        for index, header in enumerate(headers, 1):
            cell = sheet.cell(header_row, index, header)
            cell.fill = PatternFill("solid", fgColor=COPPER)
            cell.font = Font(bold=True, color=WHITE)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.row_dimensions[header_row].height = 28

        border = Border(bottom=Side(style="thin", color="DDD8D0"))
        for row_index, record in enumerate(raw, header_row + 1):
            for col_index, value in enumerate(record, 1):
                cell = sheet.cell(row_index, col_index, excel_value(value))
                cell.border = border
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if row_index % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="FAF8F4")
                if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
                    cell.number_format = '#,##0.00'

        if headers:
            sheet.freeze_panes = f"A{header_row + 1}"
            sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(width)}{max(header_row, sheet.max_row)}"
        for index, header in enumerate(headers, 1):
            lengths = [len(str(header))]
            lengths.extend(len(row[index - 1]) for row in shown[:200] if index - 1 < len(row))
            sheet.column_dimensions[get_column_letter(index)].width = min(max(max(lengths) + 3, 12), 42)
        sheet.sheet_view.showGridLines = False
        sheet.print_title_rows = "$%s:$%s" % (header_row, header_row)
        sheet.page_setup.orientation = "landscape" if width > 6 else "portrait"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.oddFooter.center.text = f"{company.name} · KOFAD generated export"
        sheet.oddFooter.right.text = "Page &P of &N"
        book.save(output)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    elif format == "docx":
        doc = WordDocument()
        section = doc.sections[0]
        section.orientation = WD_ORIENT.LANDSCAPE if len(headers) > 6 else WD_ORIENT.PORTRAIT
        if section.orientation == WD_ORIENT.LANDSCAPE:
            section.page_width, section.page_height = Inches(11.7), Inches(8.3)
        else:
            section.page_width, section.page_height = Inches(8.3), Inches(11.7)
        section.left_margin = section.right_margin = Inches(.55)
        section.top_margin = section.bottom_margin = Inches(.55)
        doc.styles["Normal"].font.name = "Aptos"
        doc.styles["Normal"].font.size = Pt(9)

        p = doc.add_paragraph()
        run = p.add_run(company.name)
        run.bold = True
        run.font.size = Pt(18)
        run.font.color.rgb = RGBColor(23, 23, 23)
        p.paragraph_format.space_after = Pt(1)
        p = doc.add_paragraph()
        run = p.add_run(title)
        run.bold = True
        run.font.size = Pt(12)
        run.font.color.rgb = RGBColor(184, 115, 51)
        p.paragraph_format.space_after = Pt(8)

        if metadata:
            meta_table = doc.add_table(rows=0, cols=2)
            for key, value in metadata:
                cells = meta_table.add_row().cells
                cells[0].text, cells[1].text = key, value
                shade_word(cells[0], CREAM)
                cells[0].paragraphs[0].runs[0].bold = True
            doc.add_paragraph()

        if summary:
            summary_table = doc.add_table(rows=2, cols=min(4, len(summary)))
            for index, (key, value) in enumerate(summary[:4]):
                summary_table.cell(0, index).text = key
                summary_table.cell(1, index).text = display(value)
                shade_word(summary_table.cell(0, index), CHARCOAL)
                shade_word(summary_table.cell(1, index), CREAM)
                for run in summary_table.cell(0, index).paragraphs[0].runs:
                    run.bold = True
                    run.font.color.rgb = RGBColor(255, 255, 255)
                for run in summary_table.cell(1, index).paragraphs[0].runs:
                    run.bold = True
                    run.font.size = Pt(11)
            doc.add_paragraph()

        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Table Grid"
        for cell, header in zip(table.rows[0].cells, headers):
            cell.text = header
            shade_word(cell, COPPER)
            for run in cell.paragraphs[0].runs:
                run.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)
                run.font.size = Pt(8)
        for index, record in enumerate(shown):
            cells = table.add_row().cells
            for cell, value in zip(cells, record):
                cell.text = value
                if index % 2:
                    shade_word(cell, "FAF8F4")
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after = Pt(0)
                    for run in paragraph.runs:
                        run.font.size = Pt(7.5)

        for note in notes:
            p = doc.add_paragraph()
            run = p.add_run(note)
            run.italic = True
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(107, 107, 107)
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer.add_run(f"{company.name} · Generated by KOFAD").font.size = Pt(7)
        doc.save(output)
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    elif format == "pdf":
        page_size = landscape(A4) if len(headers) > 6 else portrait(A4)
        styles = getSampleStyleSheet()
        body_style = ParagraphStyle("KofadBody", parent=styles["BodyText"], fontSize=6.6 if len(headers) > 6 else 7.4, leading=9)
        head_style = ParagraphStyle("KofadHead", parent=body_style, fontName="Helvetica-Bold", textColor=colors.white, leading=8)
        small_style = ParagraphStyle("KofadSmall", parent=body_style, fontSize=6.5, textColor=colors.HexColor("#666666"))
        title_style = ParagraphStyle("KofadTitle", parent=styles["Heading1"], fontSize=15, leading=18, textColor=colors.HexColor("#171717"))

        def chrome(pdf, document):
            page_w, page_h = page_size
            pdf.saveState()
            pdf.setFillColor(colors.HexColor("#171717"))
            pdf.rect(0, page_h - 10 * mm, page_w, 10 * mm, fill=1, stroke=0)
            pdf.setFillColor(colors.HexColor("#B87333"))
            pdf.rect(0, page_h - 10 * mm, 3 * mm, 10 * mm, fill=1, stroke=0)
            pdf.setFillColor(colors.white)
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawString(8 * mm, page_h - 6.4 * mm, company.name[:60])
            pdf.setFillColor(colors.HexColor("#777777"))
            pdf.setFont("Helvetica", 6.5)
            pdf.drawString(12 * mm, 7 * mm, "Generated by KOFAD · Controlled business record")
            pdf.drawRightString(page_w - 12 * mm, 7 * mm, f"Page {document.page}")
            pdf.restoreState()

        story = [Paragraph(escape(title), title_style), Spacer(1, 4)]
        if metadata:
            meta_rows = [[Paragraph(f"<b>{escape(key)}</b>", small_style), Paragraph(escape(value), small_style)] for key, value in metadata]
            meta_table = Table(meta_rows, colWidths=[36 * mm, None])
            meta_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F6F2EA")),
                ("LINEBELOW", (0, 0), (-1, -1), .25, colors.HexColor("#DDD8D0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]))
            story.extend([meta_table, Spacer(1, 7)])
        if summary:
            summary_rows = [[Paragraph(escape(key), small_style), Paragraph(f"<b>{escape(display(value))}</b>", body_style)] for key, value in summary[:4]]
            summary_table = Table(summary_rows, colWidths=[45 * mm, 45 * mm])
            summary_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6F2EA")),
                ("BOX", (0, 0), (-1, -1), .4, colors.HexColor("#DDD8D0")),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]))
            story.extend([summary_table, Spacer(1, 7)])

        cells = [[Paragraph(escape(str(value)), head_style) for value in headers]]
        cells.extend([[Paragraph(escape(value), body_style) for value in record] for record in shown])
        available_width = page_size[0] - 24 * mm
        table = Table(cells, repeatRows=1, colWidths=[available_width / max(1, len(headers))] * len(headers))
        commands = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#B87333")),
            ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#DDD8D0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for row_index in range(2, len(cells), 2):
            commands.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#FAF8F4")))
        table.setStyle(TableStyle(commands))
        story.append(table)
        if notes:
            story.append(Spacer(1, 8))
            story.extend(Paragraph(escape(note), small_style) for note in notes)

        SimpleDocTemplate(
            output, pagesize=page_size, leftMargin=12 * mm, rightMargin=12 * mm,
            topMargin=20 * mm, bottomMargin=14 * mm, title=title, author=company.name,
        ).build(story, onFirstPage=chrome, onLaterPages=chrome)
        content_type = "application/pdf"

    elif format == "csv":
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow(headers)
        writer.writerows([[safe(value) for value in record] for record in shown])
        output.write(text.getvalue().encode("utf-8-sig"))
        content_type = "text/csv"
    else:
        raise ValidationError("Unsupported export format.")

    response = HttpResponse(output.getvalue(), content_type=content_type)
    safe_filename = "".join(ch for ch in filename.lower() if ch.isalnum() or ch in "-_") or "kofad-export"
    response["Content-Disposition"] = f'attachment; filename="{safe_filename}.{format}"'
    return response
