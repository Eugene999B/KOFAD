import io
from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from .models import Branch, Company, Worker, WorkerDocument


class WorkerIdentityExperienceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            "identity-admin", "identity-admin@example.test", "identity-test-password"
        )
        self.branch = Branch.objects.create(name="Main", code="main")
        self.user.access.branches.add(self.branch)
        Company.objects.create(
            name="KOFAD IMPEX ENTERPRISE",
            phone="+233200000000",
            address="Kumasi, Ghana",
        )
        self.worker = Worker.objects.create(
            branch=self.branch,
            employee_code="KFD-ID-001",
            first_name="Ama",
            last_name="Mensah",
            phone="0240000000",
            email="ama@example.test",
            residential_address="Private Residence, Kumasi",
            department="Operations",
            job_title="Operations Officer",
            employment_type="permanent",
            status="active",
            hire_date=date(2025, 1, 6),
            blood_group="O+",
            ghana_card_number="GHA-PRIVATE-123",
            ssnit_number="SSNIT-PRIVATE-456",
            bank_name="Private Bank",
            bank_account_number="PRIVATE-ACCOUNT-789",
            emergency_name="Kwame Mensah",
            emergency_relationship="Brother",
            emergency_phone="0201112222",
            id_card_issue_date=date(2026, 10, 1),
            id_card_expiry_date=date(2028, 10, 1),
            created_by=self.user,
        )
        self.client.force_login(self.user)
        self.user.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.user.access.session_version
        session.save()

    def _photo(self):
        image = Image.new("RGB", (1800, 1200), (32, 86, 118))
        data = io.BytesIO()
        image.save(data, "PNG")
        return SimpleUploadedFile(
            "phone-camera-photo.png",
            data.getvalue(),
            content_type="image/png",
        )

    def test_profile_photo_is_normalized_compressed_and_used_as_current_photo(self):
        response = self.client.post(
            f"/workers/{self.worker.pk}/documents/",
            {
                "category": "photo",
                "title": "Profile & ID photograph",
                "file": self._photo(),
            },
        )
        self.assertEqual(response.status_code, 302)
        photo = WorkerDocument.objects.get(worker=self.worker, category="photo", is_current=True)
        self.assertEqual(photo.mime_type, "image/jpeg")
        self.assertTrue(photo.original_filename.endswith("-id.jpg"))
        self.assertLessEqual(photo.file_size_bytes, 500 * 1024)
        with Image.open(io.BytesIO(bytes(photo.file_data))) as normalized:
            self.assertEqual(normalized.size, (720, 900))
            self.assertEqual(normalized.mode, "RGB")

        second = self.client.post(
            f"/workers/{self.worker.pk}/documents/",
            {
                "category": "photo",
                "title": "Replacement portrait",
                "file": self._photo(),
            },
        )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            WorkerDocument.objects.filter(
                worker=self.worker, category="photo", is_current=True
            ).count(),
            1,
        )

    def test_worker_profile_exposes_clear_document_and_print_downloads(self):
        WorkerDocument.objects.create(
            worker=self.worker,
            category="contract",
            title="Employment contract",
            document_type="PDF",
            document_number="EMP-001",
            original_filename="employment-contract.pdf",
            mime_type="application/pdf",
            file_size_bytes=12,
            checksum_sha256="a" * 64,
            file_data=b"%PDF-1.4 TEST",
            uploaded_by=self.user,
        )
        response = self.client.get(f"/workers/{self.worker.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Download worker profile PDF")
        self.assertContains(response, "Download A4 print sheet")
        self.assertContains(response, "Download exact CR80 PDF")
        self.assertContains(response, "Download original")
        self.assertContains(response, "Employment contract")

    def test_profile_and_both_id_card_outputs_are_valid_pdfs(self):
        for path in (
            f"/workers/{self.worker.pk}/profile.pdf",
            f"/workers/{self.worker.pk}/id-card.pdf",
            f"/workers/{self.worker.pk}/id-card-print-sheet.pdf",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertTrue(response.content.startswith(b"%PDF"))

    def test_public_qr_verification_is_safe_and_excludes_private_hr_data(self):
        self.client.logout()
        response = self.client.get(f"/verify/worker/{self.worker.card_token}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Verified employee")
        self.assertContains(response, self.worker.full_name)
        self.assertContains(response, self.worker.employee_code)
        self.assertContains(response, "KOPEX")
        self.assertNotContains(response, self.worker.ghana_card_number)
        self.assertNotContains(response, self.worker.ssnit_number)
        self.assertNotContains(response, self.worker.bank_account_number)
        self.assertNotContains(response, self.worker.residential_address)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_expired_card_is_not_reported_as_current(self):
        self.worker.id_card_expiry_date = date(2026, 1, 1)
        self.worker.save(update_fields=["id_card_expiry_date"])
        self.client.logout()
        response = self.client.get(f"/verify/worker/{self.worker.card_token}/")
        self.assertContains(response, "Credential not currently active")
