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
HERITAGE_LOGO = os.path.join(settings.BASE_DIR, "static", "brand", "kofad-emblem.png")


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
    return f"KPX-{worker.employee_code}-{worker.card_token.hex[:6].upper()}"


def _logo_reader():
    try:
        return ImageReader(HERITAGE_LOGO)
    except Exception:
        return None


def _draw_logo(pdf, x, y, size):
    pdf.setFillColor(colors.white)
    pdf.roundRect(x, y, size, size, 2.2 * mm, fill=1, stroke=0)
    logo = _logo_reader()
    if logo:
        try:
            pdf.drawImage(
                logo, x + 1 * mm, y + 1 * mm, size - 2 * mm, size - 2 * mm,
                preserveAspectRatio=True, anchor="c", mask="auto",
            )
            return
        except Exception:
            pass
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(x + size / 2, y + size / 2 - 2, "KOPEX")


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
    issue, expiry = _card_dates(worker)
    serial = _card_serial(worker)
    scale = width / (85.60 * mm)

    pdf.saveState()
    pdf.setFillColor(colors.white)
    pdf.roundRect(x, y, width, height, 2.1 * mm * scale, fill=1, stroke=0)
    pdf.setFillColor(NAVY_DARK)
    pdf.roundRect(x, y + height - 14.5 * mm * scale, width, 14.5 * mm * scale, 2.1 * mm * scale, fill=1, stroke=0)
    pdf.setFillColor(TEAL)
    pdf.rect(x, y + height - 15.4 * mm * scale, width, .9 * mm * scale, fill=1, stroke=0)
    pdf.setFillColor(GOLD)
    pdf.rect(x, y, 2.2 * mm * scale, height, fill=1, stroke=0)

    # Subtle security lines, deliberately quiet enough to keep the portrait readable.
    pdf.saveState()
    pdf.setStrokeColor(colors.HexColor("#DDE9EE"))
    pdf.setLineWidth(.22)
    for index in range(9):
        yy = y + (5 + index * 4.1) * mm * scale
        pdf.bezier(
            x + 32 * mm * scale, yy,
            x + 46 * mm * scale, yy + 4 * mm * scale,
            x + 64 * mm * scale, yy - 4 * mm * scale,
            x + width - 4 * mm * scale, yy,
        )
        pdf.stroke()
    pdf.restoreState()

    _draw_logo(pdf, x + 5 * mm * scale, y + height - 12.2 * mm * scale, 9.4 * mm * scale)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 10 * scale)
    pdf.drawString(x + 17 * mm * scale, y + height - 7.0 * mm * scale, "KOPEX")
    pdf.setFillColor(GOLD)
    pdf.setFont("Helvetica-Bold", 5.4 * scale)
    pdf.drawString(x + 17 * mm * scale, y + height - 10.2 * mm * scale, "IMPEX · OFFICIAL STAFF IDENTIFICATION")
    legal_name = str(company.name or "").strip()
    if legal_name and "KOPEX" not in legal_name.upper():
        pdf.setFillColor(colors.HexColor("#C7D7E1"))
        pdf.setFont("Helvetica", 3.8 * scale)
        pdf.drawRightString(x + width - 4 * mm * scale, y + height - 10.1 * mm * scale, legal_name[:42])

    _draw_worker_photo(
        pdf, worker, x + 6 * mm * scale, y + 7.2 * mm * scale,
        24.2 * mm * scale, 30.3 * mm * scale, radius=1.8 * mm * scale,
    )

    text_x = x + 34 * mm * scale
    text_w = width - 39 * mm * scale
    pdf.setFillColor(TEAL)
    pdf.roundRect(text_x, y + 33.0 * mm * scale, 25 * mm * scale, 5.2 * mm * scale, 2.6 * mm * scale, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 5.4 * scale)
    pdf.drawCentredString(text_x + 12.5 * mm * scale, y + 34.75 * mm * scale, worker.employee_code[:22])

    _fit_text(pdf, worker.full_name.upper(), text_x, y + 27.8 * mm * scale, text_w, 10.2 * scale, 6.2 * scale, color=NAVY_DARK)
    _fit_text(pdf, worker.job_title.upper(), text_x, y + 23.1 * mm * scale, text_w, 6.1 * scale, 4.4 * scale, color=TEAL)
    _fit_text(pdf, worker.department or "Operations", text_x, y + 19.2 * mm * scale, text_w, 5.2 * scale, 4.0 * scale, font="Helvetica", color=MUTED)

    labels = [
        ("LOCATION", worker.branch.name),
        ("ISSUED", issue.strftime("%d %b %Y")),
        ("VALID UNTIL", expiry.strftime("%d %b %Y") if expiry else "Employment duration"),
    ]
    base_y = 14.5 * mm * scale
    col_width = text_w / 3
    for index, (label, value) in enumerate(labels):
        xx = text_x + index * col_width
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica-Bold", 3.5 * scale)
        pdf.drawString(xx, y + base_y, label)
        _fit_text(pdf, value, xx, y + base_y - 3.0 * mm * scale, col_width - 1.5 * mm * scale, 4.7 * scale, 3.4 * scale, color=INK)

    pdf.setFillColor(PAPER)
    pdf.roundRect(text_x, y + 4.0 * mm * scale, text_w, 4.3 * mm * scale, 2.1 * mm * scale, fill=1, stroke=0)
    status_color = TEAL if worker.status == "active" else colors.HexColor("#A34B43")
    pdf.setFillColor(status_color)
    pdf.setFont("Helvetica-Bold", 4.2 * scale)
    pdf.drawString(text_x + 2.0 * mm * scale, y + 5.35 * mm * scale, worker.get_status_display().upper())
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 3.6 * scale)
    pdf.drawRightString(x + width - 4.2 * mm * scale, y + 5.35 * mm * scale, f"CARD {serial}"[:48])

    pdf.setStrokeColor(NAVY)
    pdf.setLineWidth(.65 * scale)
    pdf.roundRect(x, y, width, height, 2.1 * mm * scale, fill=0, stroke=1)
    pdf.restoreState()


def _draw_card_back(pdf, request, worker, company, width, height, x=0, y=0):
    issue, expiry = _card_dates(worker)
    serial = _card_serial(worker)
    verify_url = _verification_url(request, worker)
    scale = width / (85.60 * mm)

    pdf.saveState()
    pdf.setFillColor(PAPER)
    pdf.roundRect(x, y, width, height, 2.1 * mm * scale, fill=1, stroke=0)
    pdf.setFillColor(NAVY_DARK)
    pdf.roundRect(x, y + height - 12 * mm * scale, width, 12 * mm * scale, 2.1 * mm * scale, fill=1, stroke=0)
    pdf.setFillColor(GOLD)
    pdf.rect(x, y + height - 12.8 * mm * scale, width, .8 * mm * scale, fill=1, stroke=0)

    _draw_logo(pdf, x + 5 * mm * scale, y + height - 10.1 * mm * scale, 7.8 * mm * scale)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 8.1 * scale)
    pdf.drawString(x + 15.3 * mm * scale, y + height - 6.6 * mm * scale, "KOPEX IMPEX")
    pdf.setFillColor(GOLD)
    pdf.setFont("Helvetica-Bold", 4.2 * scale)
    pdf.drawString(x + 15.3 * mm * scale, y + height - 9.5 * mm * scale, "SECURE WORKFORCE CREDENTIAL")

    qr_size = 22 * mm * scale
    qr_x = x + width - qr_size - 5 * mm * scale
    qr_y = y + 16.5 * mm * scale
    pdf.setFillColor(colors.white)
    pdf.roundRect(qr_x - 1.6 * mm * scale, qr_y - 1.6 * mm * scale, qr_size + 3.2 * mm * scale, qr_size + 3.2 * mm * scale, 2 * mm * scale, fill=1, stroke=0)
    _draw_qr(pdf, verify_url, qr_x, qr_y, qr_size)
    pdf.setFillColor(TEAL)
    pdf.setFont("Helvetica-Bold", 3.8 * scale)
    pdf.drawCentredString(qr_x + qr_size / 2, y + 12.4 * mm * scale, "SCAN TO VERIFY")

    left_x = x + 5.5 * mm * scale
    left_w = width - qr_size - 14 * mm * scale
    pdf.setFillColor(colors.white)
    pdf.roundRect(left_x, y + 25.0 * mm * scale, left_w, 12.3 * mm * scale, 1.6 * mm * scale, fill=1, stroke=0)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 4.4 * scale)
    pdf.drawString(left_x + 2 * mm * scale, y + 34.2 * mm * scale, "EMERGENCY CONTACT")
    _fit_text(pdf, worker.emergency_name or "Not recorded", left_x + 2 * mm * scale, y + 30.6 * mm * scale, left_w - 4 * mm * scale, 5.3 * scale, 4 * scale, color=INK)
    emergency_detail = " · ".join(filter(None, [worker.emergency_relationship, worker.emergency_phone])) or "No emergency details recorded"
    _fit_text(pdf, emergency_detail, left_x + 2 * mm * scale, y + 27.1 * mm * scale, left_w - 4 * mm * scale, 4.3 * scale, 3.4 * scale, font="Helvetica", color=MUTED)

    pdf.setFillColor(colors.white)
    pdf.roundRect(left_x, y + 13.0 * mm * scale, left_w, 9.4 * mm * scale, 1.6 * mm * scale, fill=1, stroke=0)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 4.2 * scale)
    pdf.drawString(left_x + 2 * mm * scale, y + 19.3 * mm * scale, "CREDENTIAL")
    _fit_text(pdf, f"Serial: {serial}", left_x + 2 * mm * scale, y + 16.5 * mm * scale, left_w - 4 * mm * scale, 4.0 * scale, 3.2 * scale, font="Helvetica", color=INK)
    validity = f"Issued {issue:%d %b %Y}" + (f" · Expires {expiry:%d %b %Y}" if expiry else "")
    _fit_text(pdf, validity, left_x + 2 * mm * scale, y + 13.8 * mm * scale, left_w - 4 * mm * scale, 3.7 * scale, 3.0 * scale, font="Helvetica", color=MUTED)

    # Machine-readable visual security bars derived from the unique token.
    token = worker.card_token.hex
    bar_x = left_x
    bar_y = y + 9.2 * mm * scale
    bar_w = left_w
    gap = bar_w / 32
    for index in range(32):
        code = int(token[index], 16)
        pdf.setStrokeColor(GOLD if index % 7 == 0 else NAVY)
        pdf.setLineWidth((.20 + (code % 3) * .11) * mm * scale)
        height_bar = (1.8 + (code % 5) * .45) * mm * scale
        xx = bar_x + index * gap
        pdf.line(xx, bar_y, xx, bar_y + height_bar)

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 3.25 * scale)
    disclaimer = "Company property. Not a national identity document. Alteration, transfer or unauthorized duplication is prohibited."
    pdf.drawCentredString(x + width / 2, y + 5.6 * mm * scale, disclaimer)
    found = "IF FOUND: " + " · ".join(filter(None, [str(company.phone or "").strip(), str(company.address or "").strip().replace("\n", " ")]))
    _fit_text(pdf, found or "IF FOUND: Return to KOPEX IMPEX", x + 5 * mm * scale, y + 2.6 * mm * scale, width - 10 * mm * scale, 3.3 * scale, 2.8 * scale, font="Helvetica-Bold", color=NAVY)

    pdf.setStrokeColor(NAVY)
    pdf.setLineWidth(.65 * scale)
    pdf.roundRect(x, y, width, height, 2.1 * mm * scale, fill=0, stroke=1)
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
    pdf.setTitle(f"KOPEX Staff ID Card - {worker.full_name}")
    pdf.setAuthor("KOPEX IMPEX workforce system")

    _draw_card_front(pdf, worker, company, width, height)
    pdf.showPage()
    _draw_card_back(pdf, request, worker, company, width, height)
    pdf.save()

    audit(request.user, branch, "worker.id_card.downloaded", worker.employee_code, {
        "layout": "CR80", "serial": _card_serial(worker), "qr_verification": True,
    })
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="kopex-id-{worker.employee_code}.pdf"'
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
    pdf.setTitle(f"KOPEX Staff ID Print Sheet - {worker.full_name}")

    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(18 * mm, page_height - 20 * mm, "KOPEX")
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
    pdf.drawCentredString(page_width / 2, 14 * mm, "KOPEX IMPEX · Controlled workforce credential · Print at 100% scale")
    pdf.save()

    audit(request.user, branch, "worker.id_card.downloaded", worker.employee_code, {
        "layout": "A4 print sheet", "serial": _card_serial(worker), "qr_verification": True,
    })
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="kopex-id-print-sheet-{worker.employee_code}.pdf"'
    return response


@protected("manage_company")
def worker_profile_pdf(request, branch, pk):
    worker = get_object_or_404(Worker.objects.select_related("branch"), pk=pk, branch=branch)
    company = Company.objects.first() or Company()
    documents = list(worker.documents.exclude(category="photo").order_by("-created_at"))
    output = io.BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.setTitle(f"Worker Profile - {worker.full_name}")
    pdf.setAuthor("KOPEX IMPEX workforce system")

    def header(page_label="WORKER PROFILE"):
        pdf.setFillColor(NAVY_DARK)
        pdf.rect(0, page_height - 38 * mm, page_width, 38 * mm, fill=1, stroke=0)
        _draw_logo(pdf, 15 * mm, page_height - 29 * mm, 18 * mm)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(39 * mm, page_height - 18 * mm, "KOPEX")
        pdf.setFillColor(GOLD)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(39 * mm, page_height - 24 * mm, f"IMPEX · {page_label}")
        pdf.setFillColor(colors.HexColor("#C9D9E4"))
        pdf.setFont("Helvetica", 7)
        pdf.drawRightString(page_width - 15 * mm, page_height - 20 * mm, company.name[:52])

    def label_value(label, value, x, y, width=76 * mm):
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica-Bold", 6)
        pdf.drawString(x, y, label.upper())
        _fit_text(pdf, value or "—", x, y - 4 * mm, width, 8, 6, font="Helvetica", color=INK)

    header()
    _draw_worker_photo(pdf, worker, 16 * mm, page_height - 86 * mm, 34 * mm, 42 * mm, radius=2 * mm)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(58 * mm, page_height - 54 * mm, worker.full_name[:50])
    pdf.setFillColor(TEAL)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(58 * mm, page_height - 62 * mm, worker.job_title[:60])
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(58 * mm, page_height - 69 * mm, f"{worker.department or 'Operations'} · {worker.branch.name}")
    pdf.setFillColor(PAPER)
    pdf.roundRect(58 * mm, page_height - 82 * mm, 52 * mm, 7 * mm, 3.5 * mm, fill=1, stroke=0)
    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawString(61 * mm, page_height - 79.3 * mm, f"{worker.employee_code} · {worker.get_status_display().upper()}")

    section_y = page_height - 103 * mm
    sections = [
        ("Employment", [
            ("Joined", worker.hire_date.strftime("%d %b %Y")),
            ("Employment type", worker.get_employment_type_display()),
            ("Contract", f"{worker.contract_start:%d %b %Y}" if worker.contract_start else "Open / not specified"),
            ("Contract end", f"{worker.contract_end:%d %b %Y}" if worker.contract_end else "Open"),
        ]),
        ("Contact & emergency", [
            ("Phone", worker.phone), ("Email", worker.email or "—"),
            ("Digital address", worker.digital_address or "—"),
            ("Emergency", " · ".join(filter(None, [worker.emergency_name, worker.emergency_relationship, worker.emergency_phone])) or "—"),
        ]),
        ("Statutory & credential", [
            ("Ghana Card", worker.ghana_card_number or "—"), ("SSNIT", worker.ssnit_number or "—"),
            ("Tax ID", worker.tax_id or "—"), ("Blood group", worker.blood_group or "—"),
        ]),
    ]
    for title, rows in sections:
        pdf.setFillColor(NAVY)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(16 * mm, section_y, title.upper())
        pdf.setStrokeColor(TEAL)
        pdf.setLineWidth(1.4)
        pdf.line(16 * mm, section_y - 2 * mm, page_width - 16 * mm, section_y - 2 * mm)
        section_y -= 10 * mm
        for idx, (label, value) in enumerate(rows):
            col = idx % 2
            row = idx // 2
            label_value(label, value, 16 * mm + col * 90 * mm, section_y - row * 13 * mm, 80 * mm)
        section_y -= 31 * mm

    pdf.setFillColor(NAVY)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(16 * mm, section_y, "PRIVATE DOCUMENT REGISTER")
    pdf.setStrokeColor(GOLD)
    pdf.line(16 * mm, section_y - 2 * mm, page_width - 16 * mm, section_y - 2 * mm)
    section_y -= 8 * mm
    pdf.setFont("Helvetica-Bold", 6.5)
    pdf.setFillColor(MUTED)
    pdf.drawString(16 * mm, section_y, "DOCUMENT")
    pdf.drawString(93 * mm, section_y, "CATEGORY")
    pdf.drawString(126 * mm, section_y, "NUMBER")
    pdf.drawRightString(page_width - 16 * mm, section_y, "EXPIRY")
    section_y -= 5 * mm

    for index, document in enumerate(documents):
        if section_y < 25 * mm:
            pdf.showPage()
            header("WORKER PROFILE · DOCUMENTS")
            section_y = page_height - 50 * mm
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica", 7)
        _fit_text(pdf, document.title, 16 * mm, section_y, 72 * mm, 7, 5.5, font="Helvetica", color=INK)
        pdf.drawString(93 * mm, section_y, document.get_category_display()[:22])
        pdf.drawString(126 * mm, section_y, (document.document_number or "—")[:22])
        pdf.drawRightString(page_width - 16 * mm, section_y, document.expiry_date.strftime("%d %b %Y") if document.expiry_date else "—")
        pdf.setStrokeColor(LINE)
        pdf.line(16 * mm, section_y - 2.2 * mm, page_width - 16 * mm, section_y - 2.2 * mm)
        section_y -= 6.5 * mm

    if not documents:
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica-Oblique", 7)
        pdf.drawString(16 * mm, section_y, "No private documents are currently recorded.")

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 6)
    pdf.drawCentredString(page_width / 2, 12 * mm, "Confidential personnel record · Generated from KOPEX IMPEX workforce controls")
    pdf.save()

    audit(request.user, branch, "worker.profile_pdf.downloaded", worker.employee_code, {
        "documents": len(documents),
    })
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="kopex-worker-profile-{worker.employee_code}.pdf"'
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
    return export(
        rows, format, f"Workforce register · {branch.name}", shell(request)["company"], columns,
        filename="kofad-workforce-register", sheet_name="Workforce",
        metadata={"Scope": branch.name, "Status": status or "All", "Department": department or "All", "Employment": employment or "All", "Joined": f"{joined_from or 'Any'} to {joined_to or 'Any'}", "Search": q or "All", "Generated": timezone.localtime().strftime("%d %b %Y %H:%M")},
        summary={"Workers": len(rows), "Active": sum(1 for row in rows if row["status"] == "Active")},
    )
