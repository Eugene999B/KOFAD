import hashlib
import io
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from reportlab.lib import colors
from reportlab.lib.pagesizes import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .context import shell
from .exports import export
from .models import Company, Worker, WorkerDocument
from .services import audit
from .views import protected, problem


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
DOCUMENT_TYPES = {
    "application/pdf",
    "image/jpeg", "image/png", "image/webp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}


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
    if contract_start and contract_end and contract_end < contract_start:
        raise ValidationError("Contract end date cannot be before contract start date.")
    if exit_date and exit_date < hire_date:
        raise ValidationError("Exit date cannot be before hire date.")
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
    }


@protected("manage_company")
def workers(request, branch):
    rows = Worker.objects.filter(branch=branch)
    q = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "").strip()
    department = request.GET.get("department", "").strip()[:100]
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
        "department": department, "departments": departments,
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
        if not uploaded or not title:
            raise ValidationError("Choose a file and enter a document title.")
        if uploaded.size > MAX_DOCUMENT_BYTES:
            raise ValidationError("Worker documents cannot exceed 10 MB.")
        mime = (uploaded.content_type or "application/octet-stream").lower()
        allowed = PHOTO_TYPES if category == "photo" else DOCUMENT_TYPES
        if mime not in allowed:
            raise ValidationError("That file type is not allowed for this worker record.")
        data = uploaded.read()
        digest = hashlib.sha256(data).hexdigest()
        with transaction.atomic():
            if category == "photo":
                WorkerDocument.objects.filter(worker=worker, category="photo", is_current=True).update(is_current=False)
            document = WorkerDocument.objects.create(
                worker=worker, category=category, title=title,
                document_type=request.POST.get("document_type", "").strip()[:100],
                document_number=request.POST.get("document_number", "").strip()[:120],
                original_filename=os.path.basename(uploaded.name)[:220],
                mime_type=mime, file_size_bytes=len(data), checksum_sha256=digest,
                file_data=data, issued_date=_date(request.POST.get("issued_date"), "Issued date"),
                expiry_date=_date(request.POST.get("expiry_date"), "Expiry date"),
                notes=request.POST.get("notes", "").strip(), uploaded_by=request.user,
            )
            audit(request.user, branch, "worker.document.uploaded", worker.employee_code, {
                "document_id": document.pk, "title": title, "category": category,
                "sha256": digest, "bytes": len(data),
            })
        messages.success(request, "Worker document uploaded securely.")
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


@protected("manage_company")
def worker_id_card(request, branch, pk):
    worker = get_object_or_404(Worker, pk=pk, branch=branch)
    company = Company.objects.first() or Company()
    output = io.BytesIO()
    width, height = 85.60 * mm, 53.98 * mm
    pdf = canvas.Canvas(output, pagesize=(width, height))
    charcoal = colors.HexColor("#171717")
    copper = colors.HexColor("#B87333")
    cream = colors.HexColor("#F6F2EA")

    def base(side):
        pdf.setFillColor(charcoal)
        pdf.rect(0, 0, width, height, fill=1, stroke=0)
        pdf.setFillColor(copper)
        pdf.rect(0, height - 7 * mm, width, 7 * mm, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(5 * mm, height - 4.5 * mm, company.name[:42])
        pdf.setFont("Helvetica", 4.8)
        pdf.setFillColor(colors.HexColor("#C8C8C8"))
        pdf.drawRightString(width - 4 * mm, 2.8 * mm, f"KOFAD WORKFORCE · {side}")

    base("IDENTITY")
    photo = worker.documents.filter(category="photo", is_current=True).first()
    if photo:
        try:
            pdf.drawImage(ImageReader(io.BytesIO(bytes(photo.file_data))), 5 * mm, 14 * mm, 22 * mm, 27 * mm, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
    else:
        pdf.setFillColor(colors.HexColor("#2C2C2C"))
        pdf.roundRect(5 * mm, 14 * mm, 22 * mm, 27 * mm, 2 * mm, fill=1, stroke=0)
        pdf.setFillColor(copper)
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawCentredString(16 * mm, 25 * mm, (worker.first_name[:1] + worker.last_name[:1]).upper())

    x = 31 * mm
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(x, 38 * mm, worker.full_name[:30])
    pdf.setFillColor(copper)
    pdf.setFont("Helvetica-Bold", 6.5)
    pdf.drawString(x, 33.5 * mm, worker.job_title[:36].upper())
    pdf.setFillColor(cream)
    for label, value, y in [
        ("EMPLOYEE ID", worker.employee_code, 27 * mm),
        ("DEPARTMENT", worker.department or "—", 21 * mm),
        ("LOCATION", worker.branch.name, 15 * mm),
        ("PHONE", worker.phone, 9 * mm),
    ]:
        pdf.setFont("Helvetica-Bold", 4.5)
        pdf.setFillColor(colors.HexColor("#A9A9A9"))
        pdf.drawString(x, y + 2.2 * mm, label)
        pdf.setFont("Helvetica", 6)
        pdf.setFillColor(colors.white)
        pdf.drawString(x, y, str(value)[:40])
    pdf.showPage()

    base("VERIFICATION")
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(5 * mm, 38 * mm, "EMPLOYEE VERIFICATION")
    pdf.setFont("Helvetica", 6)
    pdf.setFillColor(cream)
    lines = [
        f"Employee: {worker.full_name}",
        f"ID: {worker.employee_code}",
        f"Status: {worker.get_status_display()}",
        f"Employed since: {worker.hire_date:%d %b %Y}",
    ]
    y = 32 * mm
    for line in lines:
        pdf.drawString(5 * mm, y, line[:75])
        y -= 5 * mm
    pdf.setStrokeColor(copper)
    pdf.line(5 * mm, 10 * mm, width - 5 * mm, 10 * mm)
    pdf.setFillColor(colors.HexColor("#BDBDBD"))
    pdf.setFont("Helvetica", 4.8)
    pdf.drawString(5 * mm, 6 * mm, f"Verification token: {worker.card_token}")
    pdf.save()
    audit(request.user, branch, "worker.id_card.downloaded", worker.employee_code)
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="kofad-id-{worker.employee_code}.pdf"'
    return response


@protected("manage_company")
def workers_export(request, branch, format):
    rows = []
    qs = Worker.objects.filter(branch=branch).order_by("last_name", "first_name")
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)
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
        metadata={"Scope": branch.name, "Status": status or "All", "Generated": timezone.localtime().strftime("%d %b %Y %H:%M")},
        summary={"Workers": len(rows), "Active": sum(1 for row in rows if row["status"] == "Active")},
    )
