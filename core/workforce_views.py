import hashlib
import io
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .context import shell
from .exports import export
from .models import Company, Worker, WorkerDocument
from .services import audit
from .views import protected, problem


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PHOTO_INPUT_BYTES = 25 * 1024 * 1024
MAX_PHOTO_PIXELS = 40_000_000
PHOTO_CANVAS = (720, 900)
DOCUMENT_TYPES = {
    "application/pdf",
    "image/jpeg", "image/png", "image/webp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

NAVY = colors.HexColor("#102B46")
NAVY_DARK = colors.HexColor("#0A2034")
TEAL = colors.HexColor("#138C94")
GOLD = colors.HexColor("#E9AC32")
INK = colors.HexColor("#172F47")
MUTED = colors.HexColor("#64798B")
PAPER = colors.HexColor("#F3F6FA")
LINE = colors.HexColor("#D7E2EB")
HERITAGE_LOGO = os.path.join(settings.BASE_DIR, "static", "brand", "kofad-official-logo.jpeg")


def _normalized_photo(uploaded):
    if uploaded.size > MAX_PHOTO_INPUT_BYTES:
        raise ValidationError("Profile photos cannot exceed 25 MB before compression.")
    raw = uploaded.read()
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.seek(0)
            width, height = source.size
            if width < 120 or height < 120:
                raise ValidationError("Choose a clearer photo of at least 120 × 120 pixels.")
            if width * height > MAX_PHOTO_PIXELS:
                raise ValidationError("That photo is too large to process safely.")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageOps.fit(
                image, PHOTO_CANVAS, method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.40),
            )
            encoded = b""
            for quality in (82, 76, 70, 64):
                output = io.BytesIO()
                image.save(
                    output, format="JPEG", quality=quality, optimize=True,
                    progressive=True, dpi=(300, 300),
                )
                encoded = output.getvalue()
                if len(encoded) <= 500 * 1024:
                    break
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError(
            "Choose a valid photograph. KOFAD accepts common image formats and converts them to a compact ID-ready JPEG."
        )
    stem = os.path.splitext(os.path.basename(uploaded.name or "profile-photo"))[0][:120] or "profile-photo"
    return encoded, "image/jpeg", f"{stem}-id.jpg"


def _card_dates(worker):
    issue = worker.id_card_issue_date or timezone.localtime(worker.created_at).date()
    expiry = worker.id_card_expiry_date or worker.contract_end
    return issue, expiry


def _card_serial(worker):
    return f"KFD-{worker.employee_code}-{worker.card_token.hex[:6].upper()}"


def _logo_reader():
    try:
        return ImageReader(HERITAGE_LOGO)
    except Exception:
        return None


def _draw_logo(pdf, x, y, size):
    from .brand_art import draw_mark
    pdf.setFillColor(colors.white)
    pdf.roundRect(x, y, size, size, 1.5 * mm, fill=1, stroke=0)
    draw_mark(pdf, x + size * .1, y + size * .1, size * .8)


def _draw_qr(pdf, value, x, y, size):
    qr = QrCodeWidget(value)
    qr.barFillColor = NAVY_DARK
    bounds = qr.getBounds()
    source_width = bounds[2] - bounds[0]
    source_height = bounds[3] - bounds[1]
    drawing = Drawing(
        size, size,
        transform=[size / source_width, 0, 0, size / source_height, 0, 0],
    )
    drawing.add(qr)
    renderPDF.draw(drawing, pdf, x, y)


def _fit_text(pdf, text, x, y, width, preferred=9, minimum=5, font="Helvetica-Bold", color=INK):
    from .brand_art import print_fonts
    regular, bold = print_fonts()
    font = bold if font == "Helvetica-Bold" else regular if font == "Helvetica" else font
    value = str(text or "—")
    size = preferred
    pdf.setFont(font, size)
    while size > minimum and pdf.stringWidth(value, font, size) > width:
        size -= 0.35
    if pdf.stringWidth(value, font, size) > width:
        while len(value) > 3 and pdf.stringWidth(value + "…", font, size) > width:
            value = value[:-1]
        value += "…"
    pdf.setFillColor(color)
    pdf.setFont(font, size)
    pdf.drawString(x, y, value)


def _draw_worker_photo(pdf, worker, x, y, width, height, radius=2 * mm):
    photo = worker.documents.filter(category="photo", is_current=True).first()
    pdf.setFillColor(colors.HexColor("#E9EFF4"))
    pdf.roundRect(x, y, width, height, radius, fill=1, stroke=0)
    if photo:
        try:
            pdf.drawImage(
                ImageReader(io.BytesIO(bytes(photo.file_data))),
                x, y, width, height, preserveAspectRatio=False, mask="auto",
            )
            pdf.setStrokeColor(GOLD)
            pdf.setLineWidth(1.1)
            pdf.roundRect(x, y, width, height, radius, fill=0, stroke=1)
            return
        except Exception:
            pass
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawCentredString(
        x + width / 2, y + height / 2 - 5,
        (worker.first_name[:1] + worker.last_name[:1]).upper(),
    )
    pdf.setStrokeColor(GOLD)
    pdf.roundRect(x, y, width, height, radius, fill=0, stroke=1)


def _verification_url(request, worker):
    return request.build_absolute_uri(reverse("worker_card_verify", kwargs={"token": worker.card_token}))


def _date(value, label):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{label} must be a valid date.")


def _money(value, label):
    try:
        number = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValidationError(f"{label} must be a valid amount.")
    if number < 0:
        raise ValidationError(f"{label} cannot be negative.")
    return number


def _code():
    last = Worker.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
    return f"KFD-{last + 1:04d}"


def _worker_payload(request):
    first_name = request.POST.get("first_name", "").strip()[:80]
    last_name = request.POST.get("last_name", "").strip()[:80]
    phone = request.POST.get("phone", "").strip()[:30]
    job_title = request.POST.get("job_title", "").strip()[:120]
    hire_date = _date(request.POST.get("hire_date"), "Hire date")
    if not all([first_name, last_name, phone, job_title, hire_date]):
        raise ValidationError("First name, last name, phone, job title and hire date are required.")
    contract_start = _date(request.POST.get("contract_start"), "Contract start")
    contract_end = _date(request.POST.get("contract_end"), "Contract end")
    exit_date = _date(request.POST.get("exit_date"), "Exit date")
    card_issue = _date(request.POST.get("id_card_issue_date"), "ID card issue date")
    card_expiry = _date(request.POST.get("id_card_expiry_date"), "ID card expiry date")
    if contract_start and contract_end and contract_end < contract_start:
        raise ValidationError("Contract end date cannot be before contract start date.")
    if exit_date and exit_date < hire_date:
        raise ValidationError("Exit date cannot be before hire date.")
    if card_issue and card_expiry and card_expiry < card_issue:
        raise ValidationError("ID card expiry date cannot be before its issue date.")
    return {
        "employee_code": request.POST.get("employee_code", "").strip()[:30] or _code(),
        "first_name": first_name,
        "last_name": last_name,
        "other_names": request.POST.get("other_names", "").strip()[:120],
        "preferred_name": request.POST.get("preferred_name", "").strip()[:80],
        "gender": request.POST.get("gender", "").strip()[:20],
        "date_of_birth": _date(request.POST.get("date_of_birth"), "Date of birth"),
        "nationality": request.POST.get("nationality", "").strip()[:60] or "Ghanaian",
        "marital_status": request.POST.get("marital_status", "").strip()[:30],
        "blood_group": request.POST.get("blood_group", "").strip()[:12],
        "phone": phone,
        "alternate_phone": request.POST.get("alternate_phone", "").strip()[:30],
        "email": request.POST.get("email", "").strip()[:254],
        "residential_address": request.POST.get("residential_address", "").strip(),
        "digital_address": request.POST.get("digital_address", "").strip()[:80],
        "ghana_card_number": request.POST.get("ghana_card_number", "").strip()[:40],
        "tax_id": request.POST.get("tax_id", "").strip()[:50],
        "ssnit_number": request.POST.get("ssnit_number", "").strip()[:50],
        "department": request.POST.get("department", "").strip()[:100],
        "job_title": job_title,
        "employment_type": request.POST.get("employment_type", "permanent"),
        "status": request.POST.get("status", "active"),
        "hire_date": hire_date,
        "contract_start": contract_start,
        "contract_end": contract_end,
        "exit_date": exit_date,
        "exit_reason": request.POST.get("exit_reason", "").strip(),
        "salary_basis": request.POST.get("salary_basis", "monthly"),
        "base_salary": _money(request.POST.get("base_salary"), "Base salary"),
        "recurring_allowance": _money(request.POST.get("recurring_allowance"), "Recurring allowance"),
        "ssnit_enabled": request.POST.get("ssnit_enabled") == "on",
        "tax_mode": request.POST.get("tax_mode", "resident"),
        "junior_staff": request.POST.get("junior_staff") == "on",
        "bank_name": request.POST.get("bank_name", "").strip()[:100],
        "bank_branch": request.POST.get("bank_branch", "").strip()[:100],
        "bank_account_name": request.POST.get("bank_account_name", "").strip()[:120],
        "bank_account_number": request.POST.get("bank_account_number", "").strip()[:80],
        "momo_network": request.POST.get("momo_network", "").strip()[:40],
        "momo_number": request.POST.get("momo_number", "").strip()[:30],
        "emergency_name": request.POST.get("emergency_name", "").strip()[:120],
        "emergency_relationship": request.POST.get("emergency_relationship", "").strip()[:60],
        "emergency_phone": request.POST.get("emergency_phone", "").strip()[:30],
        "notes": request.POST.get("notes", "").strip(),
        "id_card_issue_date": card_issue,
        "id_card_expiry_date": card_expiry,
    }


@protected("manage_company")
def workers(request, branch):
    rows = Worker.objects.filter(branch=branch)
    q = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "").strip()
    department = request.GET.get("department", "").strip()[:100]
    employment = request.GET.get("employment", "").strip()[:20]
    joined_from = request.GET.get("joined_from", "").strip()
    joined_to = request.GET.get("joined_to", "").strip()
    if q:
        rows = rows.filter(
            Q(employee_code__icontains=q) | Q(first_name__icontains=q) |
            Q(last_name__icontains=q) | Q(other_names__icontains=q) |
            Q(phone__icontains=q) | Q(job_title__icontains=q)
        )
    if status:
        rows = rows.filter(status=status)
    if department:
        rows = rows.filter(department=department)
    if employment:
        rows = rows.filter(employment_type=employment)
    if joined_from:
        rows = rows.filter(hire_date__gte=_date(joined_from, "Joined from"))
    if joined_to:
        rows = rows.filter(hire_date__lte=_date(joined_to, "Joined to"))
    departments = Worker.objects.filter(branch=branch).exclude(
        department=""
    ).values_list("department", flat=True).distinct().order_by("department")
    page = Paginator(rows.select_related("branch"), 50).get_page(request.GET.get("page"))
    active_count = Worker.objects.filter(branch=branch, status="active").count()
    expiring = WorkerDocument.objects.filter(
        worker__branch=branch, is_current=True,
        expiry_date__isnull=False,
        expiry_date__lte=timezone.localdate() + timedelta(days=30),
    ).count()
    return render(request, "workers.html", {
        "title": "Workers", "page": page, "q": q, "status": status,
        "department": department, "departments": departments, "employment": employment,
        "joined_from": joined_from, "joined_to": joined_to,
        "employment_types": Worker.EMPLOYMENT_TYPES,
        "active_count": active_count, "total_count": Worker.objects.filter(branch=branch).count(),
        "expiring_documents": expiring, "statuses": Worker.STATUSES,
    })


@protected("manage_company")
def worker_edit(request, branch, pk=None):
    worker = get_object_or_404(Worker, pk=pk, branch=branch) if pk else None
    if request.method == "POST":
        try:
            payload = _worker_payload(request)
            with transaction.atomic():
                if worker:
                    before = {"name": worker.full_name, "status": worker.status, "job_title": worker.job_title}
                    for key, value in payload.items():
                        setattr(worker, key, value)
                    worker.save()
                    audit(request.user, branch, "worker.updated", worker.employee_code, {
                        "before": before, "after": {"name": worker.full_name, "status": worker.status, "job_title": worker.job_title},
                    })
                else:
                    worker = Worker.objects.create(branch=branch, created_by=request.user, **payload)
                    audit(request.user, branch, "worker.created", worker.employee_code, {
                        "name": worker.full_name, "job_title": worker.job_title,
                    })
            messages.success(request, "Worker profile saved.")
            return redirect("worker_profile", pk=worker.pk)
        except (ValidationError, ValueError, IntegrityError) as exc:
            messages.error(request, "Employee code already exists." if isinstance(exc, IntegrityError) else problem(exc))
    return render(request, "worker_form.html", {
        "title": "Edit worker" if worker else "Add worker",
        "worker": worker,
        "employment_types": Worker.EMPLOYMENT_TYPES, "statuses": Worker.STATUSES,
        "salary_basis": Worker.SALARY_BASIS, "tax_modes": Worker.TAX_MODES,
        "today": timezone.localdate().isoformat(),
    })


@protected("manage_company")
def worker_profile(request, branch, pk):
    worker = get_object_or_404(Worker.objects.select_related("branch", "user"), pk=pk, branch=branch)
    docs = worker.documents.exclude(category="photo")
    today = timezone.localdate()
    alerts = []
    if worker.contract_end and worker.contract_end <= today + timedelta(days=30):
        alerts.append(f"Contract ends on {worker.contract_end:%d %b %Y}.")
    for doc in docs.filter(is_current=True, expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=30)):
        alerts.append(f"{doc.title} expires on {doc.expiry_date:%d %b %Y}.")
    photo = worker.documents.filter(category="photo", is_current=True).first()
    return render(request, "worker_profile.html", {
        "title": worker.full_name, "worker": worker, "documents": docs,
        "photo": photo, "alerts": alerts, "document_categories": WorkerDocument.CATEGORIES,
    })


@protected("manage_company")
@require_POST
def worker_document_upload(request, branch, pk):
    worker = get_object_or_404(Worker, pk=pk, branch=branch)
    try:
        uploaded = request.FILES.get("file")
        category = request.POST.get("category", "other")
        title = request.POST.get("title", "").strip()[:180]
        if category == "photo" and not title:
            title = "Profile & ID photograph"
        if not uploaded or not title:
            raise ValidationError("Choose a file and enter a document title.")

        original_name = os.path.basename(uploaded.name)[:220]
        if category == "photo":
            data, mime, original_name = _normalized_photo(uploaded)
        else:
            if uploaded.size > MAX_DOCUMENT_BYTES:
                raise ValidationError("Worker documents cannot exceed 10 MB.")
            mime = (uploaded.content_type or "application/octet-stream").lower()
            if mime not in DOCUMENT_TYPES:
                raise ValidationError("That file type is not allowed for this worker record.")
            data = uploaded.read()

        digest = hashlib.sha256(data).hexdigest()
        with transaction.atomic():
            if category == "photo":
                WorkerDocument.objects.filter(
                    worker=worker, category="photo", is_current=True
                ).update(is_current=False)
            document = WorkerDocument.objects.create(
                worker=worker, category=category, title=title,
                document_type=(
                    "Compressed ID photograph" if category == "photo"
                    else request.POST.get("document_type", "").strip()[:100]
                ),
                document_number=request.POST.get("document_number", "").strip()[:120],
                original_filename=original_name,
                mime_type=mime, file_size_bytes=len(data), checksum_sha256=digest,
                file_data=data, issued_date=_date(request.POST.get("issued_date"), "Issued date"),
                expiry_date=_date(request.POST.get("expiry_date"), "Expiry date"),
                notes=request.POST.get("notes", "").strip(), uploaded_by=request.user,
            )
            audit(request.user, branch, "worker.document.uploaded", worker.employee_code, {
                "document_id": document.pk, "title": title, "category": category,
                "sha256": digest, "bytes": len(data),
                "compressed_photo": category == "photo",
            })
        messages.success(
            request,
            "Profile photo compressed and prepared for ID-card printing."
            if category == "photo" else "Worker document uploaded securely.",
        )
    except ValidationError as exc:
        messages.error(request, problem(exc))
    return redirect("worker_profile", pk=worker.pk)


@protected("manage_company")
def worker_document_download(request, branch, pk, document_id):
    worker = get_object_or_404(Worker, pk=pk, branch=branch)
    document = get_object_or_404(WorkerDocument, pk=document_id, worker=worker)
    audit(request.user, branch, "worker.document.downloaded", worker.employee_code, {
        "document_id": document.pk, "sha256": document.checksum_sha256,
    })
    disposition = "inline" if document.category == "photo" else "attachment"
    response = HttpResponse(bytes(document.file_data), content_type=document.mime_type)
    response["Content-Disposition"] = f'{disposition}; filename="{os.path.basename(document.original_filename)}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-SHA256"] = document.checksum_sha256
    return response


def worker_card_verify(request, token):
    worker = get_object_or_404(
        Worker.objects.select_related("branch"), card_token=token
    )
    company = Company.objects.first() or Company()
    issue, expiry = _card_dates(worker)
    response = render(request, "worker_card_verify.html", {
        "worker": worker,
        "company": company,
        "issue": issue,
        "expiry": expiry,
        "serial": _card_serial(worker),
        "credential_valid": worker.status == "active" and (not expiry or expiry >= timezone.localdate()),
    })
    response["Cache-Control"] = "no-store, private"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _draw_card_front(pdf, worker, company, width, height, x=0, y=0):
    from .brand_art import print_fonts
    regular, bold = print_fonts()
    scale = width / (85.60 * mm)
    pdf.saveState()
    pdf.translate(x, y)
    pdf.scale(scale, scale)
    width, height = 85.60 * mm, 53.98 * mm
    copper = GOLD
    pdf.setFillColor(colors.HexColor("#F6F8FA"))
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    # The official emblem forms a quiet security watermark, below all text.
    from .brand_art import draw_mark
    pdf.saveState()
    pdf.setFillAlpha(.065)
    draw_mark(pdf, 48 * mm, 9 * mm, 35 * mm)
    pdf.restoreState()
    pdf.setFillColor(NAVY_DARK)
    pdf.rect(0, height - 14 * mm, width, 14 * mm, fill=1, stroke=0)
    pdf.setFillColor(NAVY)
    corner = pdf.beginPath()
    corner.moveTo(61 * mm, height)
    corner.lineTo(width, height)
    corner.lineTo(width, height - 14 * mm)
    corner.lineTo(73 * mm, height - 14 * mm)
    corner.close()
    pdf.drawPath(corner, fill=1, stroke=0)
    pdf.setFillColor(NAVY_DARK)
    pdf.rect(0, 0, width, 8 * mm, fill=1, stroke=0)
    pdf.setFillColor(copper)
    pdf.rect(0, height - 14.6 * mm, width, .6 * mm, fill=1, stroke=0)
    _draw_logo(pdf, 4.5 * mm, height - 12 * mm, 10 * mm)
    pdf.setFillColor(colors.white)
    pdf.setFont(bold, 11)
    pdf.drawString(17 * mm, height - 7 * mm, "KOFAD")
    pdf.setFont(regular, 5.8)
    pdf.drawString(17 * mm, height - 10.5 * mm, "IMPEX ENTERPRISE")
    pdf.setFont(bold, 5)
    pdf.drawRightString(width - 4.5 * mm, height - 7 * mm, "STAFF")
    pdf.setFont(regular, 4.2)
    pdf.drawRightString(width - 4.5 * mm, height - 10 * mm, "IDENTITY CARD")
    pdf.setFillColor(colors.white)
    pdf.roundRect(4.2 * mm, 9.7 * mm, 24.6 * mm, 29.1 * mm, 1.4 * mm, fill=1, stroke=0)
    _draw_worker_photo(pdf, worker, 5 * mm, 10.5 * mm, 23 * mm, 27.5 * mm, radius=1 * mm)

    tx, tw = 32 * mm, 49 * mm
    words = worker.full_name.upper().split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if pdf.stringWidth(candidate, bold, 8.5) > tw and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    for index, line in enumerate(lines):
        _fit_text(pdf, line, tx, (35 - index * 4) * mm, tw, 8.5, 6.5)
    _fit_text(pdf, worker.job_title, tx, 25 * mm, tw, 6.8, 5.5, color=NAVY)
    _fit_text(pdf, worker.department or "Operations", tx, 21 * mm, tw, 6, 5.2, font="Helvetica", color=MUTED)
    _fit_text(pdf, worker.branch.name, tx, 17 * mm, tw, 6, 5.2, font="Helvetica", color=MUTED)
    pdf.setFillColor(colors.HexColor("#E5EEE9") if worker.status == "active" else colors.HexColor("#F6E5DD"))
    pdf.roundRect(tx, 10.4 * mm, tw, 4.7 * mm, 1.4 * mm, fill=1, stroke=0)
    _fit_text(pdf, worker.get_status_display().upper(), tx + 2 * mm, 12 * mm, tw - 4 * mm, 5.7, 5.2, color=NAVY)
    pdf.setFillColor(colors.white)
    pdf.setFont(regular, 5)
    pdf.drawString(5 * mm, 3 * mm, "EMPLOYEE")
    pdf.setFont(bold, 7)
    pdf.drawRightString(width - 5 * mm, 2.8 * mm, worker.employee_code)
    pdf.setStrokeColor(LINE)
    pdf.setLineWidth(.5)
    pdf.rect(0, 0, width, height, fill=0, stroke=1)
    pdf.restoreState()


def _draw_card_back(pdf, request, worker, company, width, height, x=0, y=0):
    from .brand_art import print_fonts
    regular, bold = print_fonts()
    issue, expiry = _card_dates(worker)
    scale = width / (85.60 * mm)
    pdf.saveState()
    pdf.translate(x, y)
    pdf.scale(scale, scale)
    width, height = 85.60 * mm, 53.98 * mm
    pdf.setFillColor(colors.white)
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setFillColor(NAVY_DARK)
    pdf.rect(0, height - 10 * mm, width, 10 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont(bold, 7)
    pdf.drawString(5 * mm, height - 6.2 * mm, "KOFAD IMPEX ENTERPRISE")
    _draw_qr(pdf, _verification_url(request, worker), 56 * mm, 16.8 * mm, 24 * mm)
    pdf.setFillColor(NAVY)
    pdf.setFont(bold, 5.3)
    pdf.drawCentredString(68 * mm, 13.5 * mm, "VERIFY STAFF STATUS")
    pairs = [
        ("CARD SERIAL", _card_serial(worker)),
        ("ISSUED / VALID UNTIL", issue.strftime("%d %b %Y") + " / " + (expiry.strftime("%d %b %Y") if expiry else "While employed")),
        ("EMERGENCY CONTACT", worker.emergency_name or "Contact the company"),
        ("EMERGENCY PHONE", worker.emergency_phone or company.phone or "See company contact"),
    ]
    for index, (label, value) in enumerate(pairs):
        yy = (38.5 - index * 7.1) * mm
        pdf.setFillColor(MUTED)
        pdf.setFont(bold, 4.7)
        pdf.drawString(5 * mm, yy, label)
        _fit_text(pdf, value, 5 * mm, yy - 3 * mm, 47 * mm, 6.1, 5, font="Helvetica")
    pdf.setStrokeColor(LINE)
    pdf.line(5 * mm, 10.5 * mm, width - 5 * mm, 10.5 * mm)
    _fit_text(pdf, "Company property. Return this card when employment ends.", 5 * mm, 7.5 * mm, width - 10 * mm, 5, 4.8, font="Helvetica", color=MUTED)
    contact = "IF FOUND: " + (company.phone or "Return to KOFAD IMPEX ENTERPRISE")
    _fit_text(pdf, contact, 5 * mm, 4 * mm, width - 10 * mm, 5.5, 5, color=NAVY)
    pdf.setStrokeColor(LINE)
    pdf.setLineWidth(.5)
    pdf.rect(0, 0, width, height, fill=0, stroke=1)
    pdf.restoreState()


def _draw_cut_marks(pdf, x, y, width, height):
    length = 3 * mm
    gap = 1.2 * mm
    pdf.setStrokeColor(colors.HexColor("#758898"))
    pdf.setLineWidth(.35)
    for xx in (x, x + width):
        direction = -1 if xx == x else 1
        pdf.line(xx + direction * gap, y, xx + direction * (gap + length), y)
        pdf.line(xx + direction * gap, y + height, xx + direction * (gap + length), y + height)
    for yy in (y, y + height):
        direction = -1 if yy == y else 1
        pdf.line(x, yy + direction * gap, x, yy + direction * (gap + length))
        pdf.line(x + width, yy + direction * gap, x + width, yy + direction * (gap + length))


@protected("manage_company")
def worker_id_card(request, branch, pk):
    worker = get_object_or_404(Worker.objects.select_related("branch"), pk=pk, branch=branch)
    company = Company.objects.first() or Company()
    output = io.BytesIO()
    width, height = 85.60 * mm, 53.98 * mm
    pdf = canvas.Canvas(output, pagesize=(width, height))
    pdf.setTitle(f"KOFAD Staff ID Card - {worker.full_name}")
    pdf.setAuthor("KOFAD IMPEX ENTERPRISE workforce system")

    _draw_card_front(pdf, worker, company, width, height)
    pdf.showPage()
    _draw_card_back(pdf, request, worker, company, width, height)
    pdf.save()

    audit(request.user, branch, "worker.id_card.downloaded", worker.employee_code, {
        "layout": "CR80", "serial": _card_serial(worker), "qr_verification": True,
    })
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="kofad-id-{worker.employee_code}.pdf"'
    return response


@protected("manage_company")
def worker_id_card_sheet(request, branch, pk):
    worker = get_object_or_404(Worker.objects.select_related("branch"), pk=pk, branch=branch)
    company = Company.objects.first() or Company()
    output = io.BytesIO()
    page_width, page_height = A4
    card_width, card_height = 85.60 * mm, 53.98 * mm
    gap = 10 * mm
    start_x = (page_width - (card_width * 2 + gap)) / 2
    card_y = page_height - 104 * mm
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.setTitle(f"KOFAD Staff ID Print Sheet - {worker.full_name}")

    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(18 * mm, page_height - 20 * mm, "KOFAD")
    pdf.setFillColor(TEAL)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(18 * mm, page_height - 26 * mm, "IMPEX · STAFF ID CARD PRINT SHEET")
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7.5)
    pdf.drawRightString(page_width - 18 * mm, page_height - 20 * mm, worker.full_name)
    pdf.drawRightString(page_width - 18 * mm, page_height - 25 * mm, worker.employee_code)

    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawCentredString(start_x + card_width / 2, card_y + card_height + 6 * mm, "FRONT")
    pdf.drawCentredString(start_x + card_width + gap + card_width / 2, card_y + card_height + 6 * mm, "BACK")

    _draw_card_front(pdf, worker, company, card_width, card_height, start_x, card_y)
    _draw_card_back(pdf, request, worker, company, card_width, card_height, start_x + card_width + gap, card_y)
    _draw_cut_marks(pdf, start_x, card_y, card_width, card_height)
    _draw_cut_marks(pdf, start_x + card_width + gap, card_y, card_width, card_height)

    box_y = card_y - 48 * mm
    pdf.setFillColor(PAPER)
    pdf.roundRect(18 * mm, box_y, page_width - 36 * mm, 35 * mm, 4 * mm, fill=1, stroke=0)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(24 * mm, box_y + 27 * mm, "Production notes")
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica", 8)
    notes = [
        "1. Print at Actual Size / 100%. Do not use Fit to Page or Shrink.",
        "2. Finished card size is ISO/IEC ID-1 / CR80: 85.60 × 53.98 mm.",
        "3. Use a duplex CR80 card printer or print/cut/laminate on suitable card stock.",
        "4. Verify portrait, employee ID, issue/expiry dates and QR scan before final production.",
        "5. QR verification exposes only safe employment identity details; private HR data stays protected.",
    ]
    yy = box_y + 21 * mm
    for note in notes:
        pdf.drawString(24 * mm, yy, note)
        yy -= 4.6 * mm

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7)
    pdf.drawCentredString(page_width / 2, 14 * mm, "KOFAD IMPEX ENTERPRISE · Controlled workforce credential · Print at 100% scale")
    pdf.save()

    audit(request.user, branch, "worker.id_card.downloaded", worker.employee_code, {
        "layout": "A4 print sheet", "serial": _card_serial(worker), "qr_verification": True,
    })
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="kofad-id-print-sheet-{worker.employee_code}.pdf"'
    return response


@protected("manage_company")
def worker_profile_pdf(request, branch, pk):
    from xml.sax.saxutils import escape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Image as PdfImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from .brand_art import print_fonts

    worker = get_object_or_404(Worker.objects.select_related("branch"), pk=pk, branch=branch)
    company = Company.objects.first() or Company()
    documents = list(worker.documents.exclude(category="photo").order_by("-created_at"))
    regular, bold = print_fonts()
    output = io.BytesIO()
    text = ParagraphStyle("Personnel", fontName=regular, fontSize=9, leading=13, textColor=INK)
    label = ParagraphStyle("PersonnelLabel", parent=text, fontName=bold, fontSize=7, leading=10, textColor=MUTED)
    title = ParagraphStyle("PersonnelName", parent=text, fontName=bold, fontSize=19, leading=23)
    section = ParagraphStyle("PersonnelSection", parent=text, fontName=bold, fontSize=11, leading=15, spaceBefore=16, spaceAfter=8, keepWithNext=True)
    def para(value, style=text):
        return Paragraph(escape(str(value or "Not recorded")).replace("\n", "<br/>"), style)
    def chrome(pdf, doc):
        pdf.saveState()
        pdf.setFillColor(NAVY_DARK)
        pdf.rect(0, A4[1] - 29 * mm, A4[0], 29 * mm, fill=1, stroke=0)
        _draw_logo(pdf, 16 * mm, A4[1] - 23 * mm, 17 * mm)
        pdf.setFillColor(colors.white)
        pdf.setFont(bold, 13)
        pdf.drawString(39 * mm, A4[1] - 13 * mm, "KOFAD IMPEX ENTERPRISE")
        pdf.setFont(regular, 8)
        pdf.drawString(39 * mm, A4[1] - 20 * mm, "CONFIDENTIAL PERSONNEL RECORD")
        pdf.setStrokeColor(LINE)
        pdf.line(16 * mm, 16 * mm, A4[0] - 16 * mm, 16 * mm)
        pdf.setFont(regular, 7)
        pdf.setFillColor(MUTED)
        pdf.drawString(16 * mm, 11 * mm, worker.employee_code + " | " + timezone.localdate().isoformat())
        pdf.drawRightString(A4[0] - 16 * mm, 11 * mm, f"Page {doc.page}")
        pdf.restoreState()

    identity = [para(worker.full_name, title), Spacer(1, 6), para(worker.job_title),
                para((worker.department or "Operations") + " | " + worker.branch.name),
                Spacer(1, 6), para(worker.employee_code + " | " + worker.get_status_display(), label)]
    photo = worker.documents.filter(category="photo", is_current=True).first()
    if photo:
        portrait = PdfImage(io.BytesIO(bytes(photo.file_data)), width=28 * mm, height=35 * mm)
    else:
        portrait = para("STAFF\nPHOTOGRAPH", label)
    intro = Table([[portrait, identity]], colWidths=[36 * mm, 142 * mm])
    intro.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"), ("LEFTPADDING",(0,0),(-1,-1),0)]))
    story = [intro]
    groups = [
        ("Employment", [
            ("Joined", worker.hire_date.strftime("%d %b %Y")), ("Employment type", worker.get_employment_type_display()),
            ("Contract start", worker.contract_start), ("Contract end", worker.contract_end),
            ("Card issued", _card_dates(worker)[0]), ("Card expires", _card_dates(worker)[1] or "While employed"),
        ]),
        ("Contact & emergency", [
            ("Phone", worker.phone), ("Email", worker.email),
            ("Residential address", worker.residential_address), ("Digital address", worker.digital_address),
            ("Emergency contact", worker.emergency_name), ("Relationship", worker.emergency_relationship),
            ("Emergency phone", worker.emergency_phone), ("Blood group", worker.blood_group),
        ]),
        ("Statutory records", [
            ("Ghana Card", worker.ghana_card_number), ("SSNIT", worker.ssnit_number), ("Tax ID", worker.tax_id),
        ]),
    ]
    for heading, values in groups:
        story.append(para(heading, section))
        cells = []
        for index in range(0, len(values), 2):
            pair = values[index:index + 2]
            row = [[para(key, label), Spacer(1, 3), para(value)] for key, value in pair]
            if len(row) == 1:
                row.append("")
            cells.append(row)
        table = Table(cells, colWidths=[89 * mm, 89 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),PAPER), ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("BOX",(0,0),(-1,-1),.4,LINE), ("INNERGRID",(0,0),(-1,-1),.3,colors.white),
            ("LEFTPADDING",(0,0),(-1,-1),10), ("RIGHTPADDING",(0,0),(-1,-1),10),
            ("TOPPADDING",(0,0),(-1,-1),9), ("BOTTOMPADDING",(0,0),(-1,-1),9),
        ]))
        story.append(table)
    story.append(para("Private document register", section))
    cells = [[para(value, label) for value in ("Document", "Category", "Number", "Expiry")]]
    cells.extend([[para(item.title), para(item.get_category_display()), para(item.document_number),
                   para(item.expiry_date or "No expiry")] for item in documents])
    if documents:
        table = Table(cells, colWidths=[65 * mm, 39 * mm, 42 * mm, 32 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),PAPER), ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LINEBELOW",(0,0),(-1,-1),.4,LINE), ("TOPPADDING",(0,0),(-1,-1),7),
            ("BOTTOMPADDING",(0,0),(-1,-1),7),
        ]))
        story.append(table)
    else:
        story.append(para("No private documents are currently recorded."))
    SimpleDocTemplate(output, pagesize=A4, leftMargin=16*mm, rightMargin=16*mm,
                      topMargin=38*mm, bottomMargin=23*mm, title="Worker Profile - " + worker.full_name,
                      author=company.name).build(story, onFirstPage=chrome, onLaterPages=chrome)
    audit(request.user, branch, "worker.profile_pdf.downloaded", worker.employee_code, {"documents": len(documents)})
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="kofad-worker-profile-{worker.employee_code}.pdf"'
    return response


@protected("manage_company")
def workers_export(request, branch, format):
    rows = []
    qs = Worker.objects.filter(branch=branch).order_by("last_name", "first_name")
    q = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "").strip()
    department = request.GET.get("department", "").strip()[:100]
    employment = request.GET.get("employment", "").strip()[:20]
    joined_from = request.GET.get("joined_from", "").strip()
    joined_to = request.GET.get("joined_to", "").strip()
    if q:
        qs = qs.filter(
            Q(employee_code__icontains=q) | Q(first_name__icontains=q) |
            Q(last_name__icontains=q) | Q(other_names__icontains=q) |
            Q(phone__icontains=q) | Q(job_title__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if department:
        qs = qs.filter(department=department)
    if employment:
        qs = qs.filter(employment_type=employment)
    if joined_from:
        qs = qs.filter(hire_date__gte=_date(joined_from, "Joined from"))
    if joined_to:
        qs = qs.filter(hire_date__lte=_date(joined_to, "Joined to"))
    for worker in qs:
        rows.append({
            "employee_code": worker.employee_code, "name": worker.full_name,
            "department": worker.department, "job_title": worker.job_title,
            "employment_type": worker.get_employment_type_display(),
            "status": worker.get_status_display(), "phone": worker.phone, "email": worker.email,
            "hire_date": worker.hire_date, "base_salary": worker.base_salary,
            "allowance": worker.recurring_allowance, "ssnit_number": worker.ssnit_number,
            "ghana_card": worker.ghana_card_number,
        })
    columns = [
        ("employee_code", "Employee ID"), ("name", "Worker"), ("department", "Department"),
        ("job_title", "Job title"), ("employment_type", "Employment type"), ("status", "Status"),
        ("phone", "Phone"), ("email", "Email"), ("hire_date", "Hire date"),
        ("base_salary", "Base salary"), ("allowance", "Recurring allowance"),
        ("ssnit_number", "SSNIT number"), ("ghana_card", "Ghana Card"),
    ]
    audit(request.user, branch, "workers.exported", format, {"rows": len(rows), "status": status})
    from .export_views import select_columns
    columns = select_columns("workers", columns)
    return export(
        rows, format, f"Workforce register · {branch.name}", shell(request)["company"], columns,
        filename="kofad-workforce-register", sheet_name="Workforce",
        metadata={"Scope": branch.name, "Status": status or "All", "Department": department or "All", "Employment": employment or "All", "Joined": f"{joined_from or 'Any'} to {joined_to or 'Any'}", "Search": q or "All", "Generated": timezone.localtime().strftime("%d %b %Y %H:%M")},
        summary={"Workers": len(rows), "Active": sum(1 for row in rows if row["status"] == "Active")},
    )
