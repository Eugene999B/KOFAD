import calendar
import hashlib
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core import accounting_engine
from core import creditors as creditor_service
from core import payroll_engine
from core import returns as return_controls
from core import services as s
from core.models import (
    Audit,
    Branch,
    Company,
    CustomerReturnRequest,
    CustomerReturnRequestLine,
    Document,
    ManagementContact,
    ManualJournal,
    Party,
    PayrollPeriod,
    ReturnPrivilege,
    Worker,
    WorkerDocument,
)


BASE_MARKER = "SHOWCASE-DATA-V1"
EXTENSION_MARKER = "SHOWCASE-EXTENDED-V2-20261004"


WORKERS = [
    {
        "employee_code": "SHOW-WRK-001", "first_name": "Michael", "last_name": "Asante",
        "preferred_name": "Michael", "gender": "Male", "date_of_birth": date(1990, 4, 18),
        "marital_status": "Married", "phone": "+233260000001", "alternate_phone": "+233500000001",
        "email": "showcase.michael.asante@example.test", "residential_address": "Ahodwo, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1001-0001", "ghana_card_number": "SHOW-GHA-000001",
        "tax_id": "SHOW-TAX-000001", "ssnit_number": "SHOW-SSNIT-000001",
        "department": "Operations", "job_title": "Operations Manager", "employment_type": "permanent",
        "hire_date": date(2024, 1, 8), "base_salary": Decimal("8500.00"), "recurring_allowance": Decimal("1200.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Kumasi", "bank_account_name": "Michael Asante",
        "bank_account_number": "SHOW-ACCT-000001", "momo_network": "MTN", "momo_number": "+233260000001",
        "emergency_name": "Showcase Emergency Contact 1", "emergency_relationship": "Spouse",
        "emergency_phone": "+233240000101", "user_key": "approver",
    },
    {
        "employee_code": "SHOW-WRK-002", "first_name": "Priscilla", "last_name": "Owusu",
        "preferred_name": "Priscilla", "gender": "Female", "date_of_birth": date(1994, 7, 11),
        "marital_status": "Single", "phone": "+233260000002", "alternate_phone": "+233500000002",
        "email": "showcase.priscilla.owusu@example.test", "residential_address": "Asokwa, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1002-0002", "ghana_card_number": "SHOW-GHA-000002",
        "tax_id": "SHOW-TAX-000002", "ssnit_number": "SHOW-SSNIT-000002",
        "department": "Sales", "job_title": "Sales Supervisor", "employment_type": "permanent",
        "hire_date": date(2024, 3, 4), "base_salary": Decimal("5200.00"), "recurring_allowance": Decimal("650.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Adum", "bank_account_name": "Priscilla Owusu",
        "bank_account_number": "SHOW-ACCT-000002", "momo_network": "MTN", "momo_number": "+233260000002",
        "emergency_name": "Showcase Emergency Contact 2", "emergency_relationship": "Sister",
        "emergency_phone": "+233240000102", "user_key": "sales",
    },
    {
        "employee_code": "SHOW-WRK-003", "first_name": "Daniel", "last_name": "Boateng",
        "preferred_name": "Daniel", "gender": "Male", "date_of_birth": date(1992, 2, 23),
        "marital_status": "Married", "phone": "+233260000003", "alternate_phone": "+233500000003",
        "email": "showcase.daniel.boateng@example.test", "residential_address": "Suame, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1003-0003", "ghana_card_number": "SHOW-GHA-000003",
        "tax_id": "SHOW-TAX-000003", "ssnit_number": "SHOW-SSNIT-000003",
        "department": "Inventory", "job_title": "Inventory Controller", "employment_type": "permanent",
        "hire_date": date(2024, 2, 12), "base_salary": Decimal("5000.00"), "recurring_allowance": Decimal("600.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Suame", "bank_account_name": "Daniel Boateng",
        "bank_account_number": "SHOW-ACCT-000003", "momo_network": "Telecel", "momo_number": "+233260000003",
        "emergency_name": "Showcase Emergency Contact 3", "emergency_relationship": "Brother",
        "emergency_phone": "+233240000103", "user_key": "inventory",
    },
    {
        "employee_code": "SHOW-WRK-004", "first_name": "Grace", "last_name": "Mensah",
        "preferred_name": "Grace", "gender": "Female", "date_of_birth": date(1995, 10, 6),
        "marital_status": "Single", "phone": "+233260000004", "alternate_phone": "+233500000004",
        "email": "showcase.grace.mensah@example.test", "residential_address": "Oforikrom, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1004-0004", "ghana_card_number": "SHOW-GHA-000004",
        "tax_id": "SHOW-TAX-000004", "ssnit_number": "SHOW-SSNIT-000004",
        "department": "Finance", "job_title": "Accounts Officer", "employment_type": "permanent",
        "hire_date": date(2024, 5, 20), "base_salary": Decimal("5600.00"), "recurring_allowance": Decimal("700.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Adum", "bank_account_name": "Grace Mensah",
        "bank_account_number": "SHOW-ACCT-000004", "momo_network": "MTN", "momo_number": "+233260000004",
        "emergency_name": "Showcase Emergency Contact 4", "emergency_relationship": "Mother",
        "emergency_phone": "+233240000104", "user_key": "finance",
    },
    {
        "employee_code": "SHOW-WRK-005", "first_name": "Samuel", "last_name": "Agyeman",
        "preferred_name": "Sam", "gender": "Male", "date_of_birth": date(1997, 1, 14),
        "marital_status": "Single", "phone": "+233260000005", "alternate_phone": "+233500000005",
        "email": "showcase.samuel.agyeman@example.test", "residential_address": "Bantama, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1005-0005", "ghana_card_number": "SHOW-GHA-000005",
        "tax_id": "SHOW-TAX-000005", "ssnit_number": "SHOW-SSNIT-000005",
        "department": "Procurement", "job_title": "Procurement Officer", "employment_type": "permanent",
        "hire_date": date(2024, 8, 5), "base_salary": Decimal("4800.00"), "recurring_allowance": Decimal("550.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Kumasi", "bank_account_name": "Samuel Agyeman",
        "bank_account_number": "SHOW-ACCT-000005", "momo_network": "MTN", "momo_number": "+233260000005",
        "emergency_name": "Showcase Emergency Contact 5", "emergency_relationship": "Father",
        "emergency_phone": "+233240000105",
    },
    {
        "employee_code": "SHOW-WRK-006", "first_name": "Linda", "last_name": "Frimpong",
        "preferred_name": "Linda", "gender": "Female", "date_of_birth": date(1998, 6, 30),
        "marital_status": "Single", "phone": "+233260000006", "alternate_phone": "+233500000006",
        "email": "showcase.linda.frimpong@example.test", "residential_address": "Tafo, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1006-0006", "ghana_card_number": "SHOW-GHA-000006",
        "tax_id": "SHOW-TAX-000006", "ssnit_number": "SHOW-SSNIT-000006",
        "department": "Sales", "job_title": "Sales Associate", "employment_type": "permanent",
        "hire_date": date(2025, 1, 6), "base_salary": Decimal("3500.00"), "recurring_allowance": Decimal("350.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Tafo", "bank_account_name": "Linda Frimpong",
        "bank_account_number": "SHOW-ACCT-000006", "momo_network": "Telecel", "momo_number": "+233260000006",
        "emergency_name": "Showcase Emergency Contact 6", "emergency_relationship": "Brother",
        "emergency_phone": "+233240000106", "junior_staff": True,
    },
    {
        "employee_code": "SHOW-WRK-007", "first_name": "Joseph", "last_name": "Antwi",
        "preferred_name": "Joe", "gender": "Male", "date_of_birth": date(1996, 9, 17),
        "marital_status": "Married", "phone": "+233260000007", "alternate_phone": "+233500000007",
        "email": "showcase.joseph.antwi@example.test", "residential_address": "Ejisu · synthetic showcase address",
        "digital_address": "SHOW-AK-1007-0007", "ghana_card_number": "SHOW-GHA-000007",
        "tax_id": "SHOW-TAX-000007", "ssnit_number": "SHOW-SSNIT-000007",
        "department": "Inventory", "job_title": "Storekeeper", "employment_type": "permanent",
        "hire_date": date(2025, 2, 10), "base_salary": Decimal("3400.00"), "recurring_allowance": Decimal("300.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Ejisu", "bank_account_name": "Joseph Antwi",
        "bank_account_number": "SHOW-ACCT-000007", "momo_network": "MTN", "momo_number": "+233260000007",
        "emergency_name": "Showcase Emergency Contact 7", "emergency_relationship": "Spouse",
        "emergency_phone": "+233240000107", "junior_staff": True,
    },
    {
        "employee_code": "SHOW-WRK-008", "first_name": "Mavis", "last_name": "Osei",
        "preferred_name": "Mavis", "gender": "Female", "date_of_birth": date(1999, 3, 9),
        "marital_status": "Single", "phone": "+233260000008", "alternate_phone": "+233500000008",
        "email": "showcase.mavis.osei@example.test", "residential_address": "Asafo, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1008-0008", "ghana_card_number": "SHOW-GHA-000008",
        "tax_id": "SHOW-TAX-000008", "ssnit_number": "SHOW-SSNIT-000008",
        "department": "Customer Service", "job_title": "Customer Service Officer", "employment_type": "permanent",
        "hire_date": date(2025, 4, 14), "base_salary": Decimal("3600.00"), "recurring_allowance": Decimal("350.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Asafo", "bank_account_name": "Mavis Osei",
        "bank_account_number": "SHOW-ACCT-000008", "momo_network": "MTN", "momo_number": "+233260000008",
        "emergency_name": "Showcase Emergency Contact 8", "emergency_relationship": "Mother",
        "emergency_phone": "+233240000108", "junior_staff": True,
    },
    {
        "employee_code": "SHOW-WRK-009", "first_name": "Richard", "last_name": "Addo",
        "preferred_name": "Richard", "gender": "Male", "date_of_birth": date(1993, 12, 2),
        "marital_status": "Married", "phone": "+233260000009", "alternate_phone": "+233500000009",
        "email": "showcase.richard.addo@example.test", "residential_address": "Kwadaso, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1009-0009", "ghana_card_number": "SHOW-GHA-000009",
        "tax_id": "SHOW-TAX-000009", "ssnit_number": "SHOW-SSNIT-000009",
        "department": "Technology", "job_title": "IT & Systems Officer", "employment_type": "permanent",
        "hire_date": date(2025, 6, 2), "base_salary": Decimal("4900.00"), "recurring_allowance": Decimal("600.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Kumasi", "bank_account_name": "Richard Addo",
        "bank_account_number": "SHOW-ACCT-000009", "momo_network": "MTN", "momo_number": "+233260000009",
        "emergency_name": "Showcase Emergency Contact 9", "emergency_relationship": "Spouse",
        "emergency_phone": "+233240000109",
    },
    {
        "employee_code": "SHOW-WRK-010", "first_name": "Beatrice", "last_name": "Nyarko",
        "preferred_name": "Beatrice", "gender": "Female", "date_of_birth": date(1991, 5, 25),
        "marital_status": "Married", "phone": "+233260000010", "alternate_phone": "+233500000010",
        "email": "showcase.beatrice.nyarko@example.test", "residential_address": "Adum, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1010-0010", "ghana_card_number": "SHOW-GHA-000010",
        "tax_id": "SHOW-TAX-000010", "ssnit_number": "SHOW-SSNIT-000010",
        "department": "Administration", "job_title": "Administrative Officer", "employment_type": "permanent",
        "hire_date": date(2025, 7, 7), "base_salary": Decimal("4200.00"), "recurring_allowance": Decimal("450.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Adum", "bank_account_name": "Beatrice Nyarko",
        "bank_account_number": "SHOW-ACCT-000010", "momo_network": "Telecel", "momo_number": "+233260000010",
        "emergency_name": "Showcase Emergency Contact 10", "emergency_relationship": "Husband",
        "emergency_phone": "+233240000110",
    },
    {
        "employee_code": "SHOW-WRK-011", "first_name": "Eric", "last_name": "Boadu",
        "preferred_name": "Eric", "gender": "Male", "date_of_birth": date(1988, 8, 8),
        "marital_status": "Married", "phone": "+233260000011", "alternate_phone": "+233500000011",
        "email": "showcase.eric.boadu@example.test", "residential_address": "Atonsu, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1011-0011", "ghana_card_number": "SHOW-GHA-000011",
        "tax_id": "SHOW-TAX-000011", "ssnit_number": "SHOW-SSNIT-000011",
        "department": "Logistics", "job_title": "Delivery Driver", "employment_type": "permanent",
        "hire_date": date(2025, 8, 11), "base_salary": Decimal("3300.00"), "recurring_allowance": Decimal("500.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Kumasi", "bank_account_name": "Eric Boadu",
        "bank_account_number": "SHOW-ACCT-000011", "momo_network": "MTN", "momo_number": "+233260000011",
        "emergency_name": "Showcase Emergency Contact 11", "emergency_relationship": "Spouse",
        "emergency_phone": "+233240000111", "junior_staff": True,
    },
    {
        "employee_code": "SHOW-WRK-012", "first_name": "Janet", "last_name": "Aidoo",
        "preferred_name": "Janet", "gender": "Female", "date_of_birth": date(2001, 11, 19),
        "marital_status": "Single", "phone": "+233260000012", "alternate_phone": "+233500000012",
        "email": "showcase.janet.aidoo@example.test", "residential_address": "Santasi, Kumasi · synthetic showcase address",
        "digital_address": "SHOW-AK-1012-0012", "ghana_card_number": "SHOW-GHA-000012",
        "tax_id": "SHOW-TAX-000012", "ssnit_number": "SHOW-SSNIT-000012",
        "department": "Operations", "job_title": "Operations Assistant", "employment_type": "probation",
        "hire_date": date(2026, 7, 6), "contract_start": date(2026, 7, 6), "contract_end": date(2027, 1, 5),
        "base_salary": Decimal("2800.00"), "recurring_allowance": Decimal("250.00"),
        "bank_name": "Showcase Bank", "bank_branch": "Kumasi", "bank_account_name": "Janet Aidoo",
        "bank_account_number": "SHOW-ACCT-000012", "momo_network": "MTN", "momo_number": "+233260000012",
        "emergency_name": "Showcase Emergency Contact 12", "emergency_relationship": "Father",
        "emergency_phone": "+233240000112", "junior_staff": True,
    },
]


USER_PROFILES = {
    "sales": ("showcase_sales", "Showcase Sales", ["operate_sales", "view_reports"]),
    "inventory": ("showcase_inventory", "Showcase Inventory", ["operate_inventory", "view_reports"]),
    "finance": ("showcase_finance", "Showcase Finance", ["operate_finance", "view_reports"]),
    "approver": ("showcase_approver", "Showcase Approver", ["approve_operations", "view_reports"]),
}


def _month_bounds(day):
    first = day.replace(day=1)
    last = day.replace(day=calendar.monthrange(day.year, day.month)[1])
    return first, last


class Command(BaseCommand):
    help = "Complete an existing synthetic showcase dataset with workforce, payroll and newer KOFAD modules."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-live-showcase", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["confirm_live_showcase"]:
            raise CommandError("Production showcase completion requires --confirm-live-showcase.")
        if not Audit.objects.filter(action="showcase.seed.completed", reference=BASE_MARKER).exists():
            self.stdout.write("No KOFAD showcase dataset detected; nothing was changed.")
            return
        if Audit.objects.filter(action="showcase.extension.completed", reference=EXTENSION_MARKER).exists():
            self.stdout.write("Extended showcase details already exist; nothing was changed.")
            return

        actor = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
        branch = Branch.objects.filter(active=True).order_by("pk").first()
        company = Company.objects.first()
        if not actor or not branch or not company:
            raise CommandError("Showcase completion requires an administrator, active location and company settings.")

        users = self._create_users(branch)
        workers = self._create_workers(actor, branch, users)
        self._create_worker_documents(actor, workers)
        self._create_payroll(actor, branch)
        creditor_bills = self._create_creditor_bills(users["finance"], branch)
        return_requests = self._create_customer_return_examples(actor, users["sales"], branch)
        journals = self._create_journals(actor, users["finance"], branch)
        self._create_return_privileges(actor, users["approver"], branch)
        contacts = self._create_management_contacts(branch)

        s.audit(
            actor, branch, "showcase.extension.completed", EXTENSION_MARKER,
            {
                "synthetic": True,
                "workers": len(workers),
                "payroll_periods": PayrollPeriod.objects.filter(branch=branch).count(),
                "direct_creditor_bills": creditor_bills,
                "customer_return_requests": return_requests,
                "manual_journals": journals,
                "management_contacts": contacts,
                "note": "Synthetic workforce and post-launch module data added for KOFAD system testing.",
            },
            category="administration", severity="notice",
        )
        self.stdout.write(self.style.SUCCESS(
            f"Showcase details completed: {len(workers)} workers plus payroll, creditor bills, "
            "advanced returns, journals, privileges and management contacts."
        ))

    def _create_users(self, branch):
        result = {}
        for key, (username, label, codenames) in USER_PROFILES.items():
            first, last = (label.split(" ", 1) + [""])[:2]
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "email": f"{username}@example.test",
                    "is_active": True,
                },
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
            permissions = Permission.objects.filter(codename__in=codenames)
            user.user_permissions.add(*permissions)
            user.access.branches.add(branch)
            result[key] = user
        return result

    def _create_workers(self, actor, branch, users):
        workers = []
        for row in WORKERS:
            data = dict(row)
            user_key = data.pop("user_key", None)
            data.setdefault("junior_staff", False)
            data.setdefault("contract_start", None)
            data.setdefault("contract_end", None)
            data.update({
                "branch": branch,
                "user": users.get(user_key) if user_key else None,
                "nationality": "Ghanaian",
                "status": "active",
                "salary_basis": "monthly",
                "ssnit_enabled": True,
                "tax_mode": "resident",
                "notes": (
                    "Synthetic showcase worker record for KOFAD testing. "
                    "Identity, payroll and contact values are demonstration data only."
                ),
                "created_by": actor,
            })
            worker, created = Worker.objects.get_or_create(
                employee_code=data["employee_code"],
                defaults={key: value for key, value in data.items() if key != "employee_code"},
            )
            if not created:
                for key, value in data.items():
                    if key != "employee_code":
                        setattr(worker, key, value)
                worker.save()
            workers.append(worker)
        return workers

    def _create_worker_documents(self, actor, workers):
        for worker in workers:
            title = "Synthetic employment profile"
            if WorkerDocument.objects.filter(worker=worker, title=title).exists():
                continue
            payload = (
                f"KOFAD SHOWCASE EMPLOYMENT RECORD\n"
                f"Employee: {worker.full_name}\n"
                f"Employee ID: {worker.employee_code}\n"
                f"Department: {worker.department}\n"
                f"Job title: {worker.job_title}\n"
                f"Hire date: {worker.hire_date.isoformat()}\n"
                "This file is synthetic demonstration evidence and is not a real employment contract.\n"
            ).encode("utf-8")
            WorkerDocument.objects.create(
                worker=worker,
                category="contract",
                title=title,
                document_type="Synthetic showcase employment record",
                document_number=f"SHOW-DOC-{worker.employee_code[-3:]}",
                original_filename=f"{worker.employee_code.lower()}-employment-record.txt",
                mime_type="text/plain",
                file_size_bytes=len(payload),
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                file_data=payload,
                issued_date=worker.hire_date,
                notes="Synthetic showcase document for secure worker-vault testing.",
                is_current=True,
                uploaded_by=actor,
            )

    def _create_payroll(self, actor, branch):
        today = timezone.localdate()
        current_first, _ = _month_bounds(today)
        previous_last = current_first - timedelta(days=1)
        previous_first, _ = _month_bounds(previous_last)

        historical = PayrollPeriod.objects.filter(
            branch=branch, year=previous_first.year, month=previous_first.month
        ).first()
        if not historical:
            historical = payroll_engine.create_period(
                actor, branch, previous_first.year, previous_first.month
            )
            for index, entry in enumerate(historical.entries.order_by("worker__employee_code"), start=1):
                entry.bonus = Decimal("300.00") if index in (1, 4) else Decimal("0.00")
                entry.overtime = Decimal("180.00") if entry.worker.junior_staff and index % 2 == 0 else Decimal("0.00")
                entry.other_deductions = Decimal("75.00") if index == 7 else Decimal("0.00")
                entry.save(update_fields=["bonus", "overtime", "other_deductions"])
            payroll_engine.recalculate_period(historical)
            historical.note = "Synthetic showcase historical payroll · approved and locked for review."
            historical.save(update_fields=["note"])
            historical, _ = payroll_engine.prepare_period(actor, historical)
            historical = payroll_engine.approve_period(actor, historical, owner_direct=True)
            payroll_engine.lock_period(actor, historical)

        current = PayrollPeriod.objects.filter(
            branch=branch, year=current_first.year, month=current_first.month
        ).first()
        if not current:
            current = payroll_engine.create_period(actor, branch, current_first.year, current_first.month)
            for index, entry in enumerate(current.entries.order_by("worker__employee_code"), start=1):
                entry.bonus = Decimal("250.00") if index == 2 else Decimal("0.00")
                entry.overtime = Decimal("120.00") if entry.worker.junior_staff and index % 3 == 0 else Decimal("0.00")
                entry.save(update_fields=["bonus", "overtime"])
            payroll_engine.recalculate_period(current)
            current.note = "Synthetic showcase current payroll · draft for testing preparation and approval."
            current.save(update_fields=["note"])

    def _create_creditor_bills(self, finance_user, branch):
        suppliers = list(Party.objects.filter(
            branch=branch, kind="supplier", name__startswith="Showcase"
        ).order_by("pk")[:3])
        specs = [
            ("SHOW-DIRECT-001", "rent", Decimal("4500.00"), -18, -3, "Synthetic showcase premises rent payable"),
            ("SHOW-DIRECT-002", "transport", Decimal("1280.00"), -7, 0, "Synthetic showcase delivery contractor payable"),
            ("SHOW-DIRECT-003", "maintenance", Decimal("2350.00"), -2, 12, "Synthetic showcase equipment maintenance payable"),
        ]
        created = 0
        today = timezone.localdate()
        for index, spec in enumerate(specs):
            if index >= len(suppliers):
                break
            external_reference, category, amount, document_offset, due_offset, note = spec
            if Document.objects.filter(
                branch=branch, kind="creditor_charge", external_reference=external_reference
            ).exists():
                continue
            creditor_service.post_creditor_bill(
                finance_user, branch,
                {
                    "party": suppliers[index].pk,
                    "amount": str(amount),
                    "document_date": (today + timedelta(days=document_offset)).isoformat(),
                    "due_date": (today + timedelta(days=due_offset)).isoformat(),
                    "external_reference": external_reference,
                    "category": category,
                    "note": note,
                },
                uuid.uuid4(),
            )
            created += 1
        return created

    def _create_customer_return_examples(self, actor, sales_user, branch):
        created = 0

        posted_return = Document.objects.filter(
            branch=branch, kind="return", reference__startswith="SHOW-RET-"
        ).select_related("original").prefetch_related("lines").order_by("reference").first()
        if posted_return and not CustomerReturnRequest.objects.filter(posted=posted_return).exists():
            return_line = posted_return.lines.first()
            if return_line and return_line.source_line_id:
                item = CustomerReturnRequest.objects.create(
                    branch=branch,
                    sale=posted_return.original,
                    refund_method="cash",
                    reason="Synthetic showcase approved customer return",
                    status="approved",
                    direct=True,
                    requested_by=actor,
                    reviewed_by=actor,
                    posted=posted_return,
                    reviewed_at=timezone.now(),
                )
                CustomerReturnRequestLine.objects.create(
                    request=item,
                    source_line=return_line.source_line,
                    quantity=return_line.quantity,
                    disposition="sellable",
                )
                created += 1

        if not CustomerReturnRequest.objects.filter(
            branch=branch, status="requested", reason__icontains="showcase pending"
        ).exists():
            sales = Document.objects.filter(
                branch=branch, kind="sale", reference__startswith="SHOW-SALE-"
            ).prefetch_related("lines").order_by("-created_at")
            for sale in sales[:80]:
                source = next(
                    (line for line in sale.lines.all() if return_controls.eligible_quantity(line) > 0),
                    None,
                )
                if not source:
                    continue
                return_controls.create_customer_return(
                    sales_user,
                    branch,
                    sale,
                    [{"line": source.pk, "quantity": 1, "disposition": "quarantine"}],
                    "Synthetic showcase pending return for approval-center testing",
                    "cash",
                )
                created += 1
                break
        return created

    def _create_journals(self, actor, finance_user, branch):
        created = 0
        today = timezone.localdate()
        if not ManualJournal.objects.filter(
            branch=branch, memo__icontains="showcase owner capital illustration"
        ).exists():
            accounting_engine.create_manual_journal(
                actor,
                branch,
                (today - timedelta(days=10)).isoformat(),
                "Synthetic showcase owner capital illustration",
                [
                    {"account_code": "1020", "debit": "12500.00", "credit": "0", "description": "Synthetic bank funding"},
                    {"account_code": "3000", "debit": "0", "credit": "12500.00", "description": "Synthetic owner capital"},
                ],
            )
            created += 1

        if not ManualJournal.objects.filter(
            branch=branch, memo__icontains="showcase adjusting journal awaiting review"
        ).exists():
            accounting_engine.create_manual_journal(
                finance_user,
                branch,
                today.isoformat(),
                "Synthetic showcase adjusting journal awaiting review",
                [
                    {"account_code": "6060", "debit": "250.00", "credit": "0", "description": "Synthetic office adjustment"},
                    {"account_code": "2400", "debit": "0", "credit": "250.00", "description": "Synthetic accrued liability"},
                ],
            )
            created += 1
        return created

    def _create_return_privileges(self, actor, approver, branch):
        ReturnPrivilege.objects.update_or_create(
            branch=branch,
            user=approver,
            defaults={
                "customer_returns": True,
                "supplier_returns": True,
                "granted_by": actor,
            },
        )

    def _create_management_contacts(self, branch):
        rows = [
            ("Showcase · Managing Director", "+233200000901"),
            ("Showcase · Finance Lead", "+233200000902"),
            ("Showcase · Operations Lead", "+233200000903"),
        ]
        created = 0
        for name, phone in rows:
            _, was_created = ManagementContact.objects.get_or_create(
                name=name,
                phone=phone,
                defaults={
                    "branch": branch,
                    "receive_closing": False,
                    "receive_low_stock": False,
                    "receive_system_alerts": False,
                    "active": True,
                },
            )
            created += int(was_created)
        return created
