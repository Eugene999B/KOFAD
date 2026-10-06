"""Generate isolated CI samples for visual review; never runs on production."""
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client
from core.exports import export
from core.models import Branch, Company, Worker, WorkerDocument

if not settings.DEBUG or not os.environ.get("GITHUB_ACTIONS"):
    raise RuntimeError("Document evidence is restricted to isolated GitHub Actions.")
out = Path("test-results/documents")
out.mkdir(parents=True, exist_ok=True)
admin = User.objects.get(username="admin")
branch = Branch.objects.get(code="main")
admin.access.branches.add(branch)
worker, _ = Worker.objects.get_or_create(employee_code="KFD-EVIDENCE-001", defaults={
    "branch": branch, "first_name": "Akosua", "last_name": "Mensah-Boateng", "other_names": "Abena",
    "job_title": "Senior Sales & Operations Officer", "department": "Commercial Operations",
    "phone": "+233240000000", "email": "sample@example.test", "hire_date": date(2024, 4, 8),
    "employment_type": "permanent", "status": "active", "emergency_name": "Kwame Mensah",
    "emergency_relationship": "Brother", "emergency_phone": "+233200000000",
    "residential_address": "Sample personnel address, Kumasi, Ashanti Region, Ghana",
    "id_card_issue_date": date(2026, 10, 6), "id_card_expiry_date": date(2028, 10, 6),
    "created_by": admin,
})
for index in range(12):
    WorkerDocument.objects.get_or_create(worker=worker, title=f"Sample employment record {index + 1}",
        defaults={"category": "contract", "document_number": f"HR-2026-{index + 1:03}",
                  "uploaded_by": admin, "original_filename": "sample.pdf", "mime_type": "application/pdf",
                  "file_data": b"%PDF SAMPLE", "file_size_bytes": 11, "checksum_sha256": "a" * 64})
client = Client(HTTP_HOST="localhost")
client.force_login(admin)
session = client.session
admin.access.refresh_from_db()
session["access_version"] = admin.access.session_version
session["branch"] = branch.pk
session["mfa_ok"] = True
session.save()
for suffix, filename in (("profile.pdf", "personnel-profile"), ("id-card.pdf", "staff-card"),
                          ("id-card-print-sheet.pdf", "staff-card-print-sheet")):
    response = client.get(f"/workers/{worker.pk}/{suffix}")
    assert response.status_code == 200, (suffix, response.status_code)
    (out / (filename + ".pdf")).write_bytes(response.content)
company = Company.objects.first()
rows = [{"name": f"Sample Trading Customer {index + 1:02}", "phone": "+233240000000",
         "outstanding": Decimal("1250.50") if index % 3 == 0 else Decimal("0.00")} for index in range(38)]
columns = [("name", "Customer"), ("phone", "Phone"), ("outstanding", "Outstanding (GHS)")]
for kind in ("pdf", "xlsx", "docx"):
    response = export(rows, kind, "Customer register", company, columns,
                      metadata={"Location": "Main · Kumasi", "Generated": "06 Oct 2026",
                                "Prepared by": "KOFAD administrator"},
                      summary={"Customers": len(rows), "Outstanding (GHS)": sum(row["outstanding"] for row in rows)},
                      notes=["Balances reflect the selected location at export time."])
    (out / ("customer-register." + kind)).write_bytes(response.content)
wide = [(f"column{index}", f"Operational field {index + 1}") for index in range(17)]
response = export([{key: f"Row {row + 1} / detail {index + 1}" for index, (key, _) in enumerate(wide)}
                   for row in range(8)], "pdf", "Wide register layout", company, wide)
(out / "wide-register.pdf").write_bytes(response.content)
