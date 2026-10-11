"""Recovery isolation: one provider/database exception cannot stop the shared worker."""
from io import StringIO
from unittest.mock import patch

from django.test import SimpleTestCase

from core.management.commands.process_sms import recover_pending_deliveries


class RecoveryIsolationTests(SimpleTestCase):
    @patch("core.management.commands.process_sms.recover_stale_whatsapp")
    @patch("core.management.commands.process_sms.recover_stale", side_effect=RuntimeError("temporary database error"))
    def test_sms_recovery_failure_does_not_block_whatsapp(self, sms_recovery, whatsapp_recovery):
        errors = StringIO()
        recover_pending_deliveries(errors)
        sms_recovery.assert_called_once_with()
        whatsapp_recovery.assert_called_once_with()
        self.assertIn("SMS recovery failed safely", errors.getvalue())

    @patch("core.management.commands.process_sms.recover_stale_whatsapp", side_effect=RuntimeError("temporary database error"))
    @patch("core.management.commands.process_sms.recover_stale")
    def test_whatsapp_recovery_failure_does_not_escape(self, sms_recovery, whatsapp_recovery):
        errors = StringIO()
        recover_pending_deliveries(errors)
        sms_recovery.assert_called_once_with()
        whatsapp_recovery.assert_called_once_with()
        self.assertIn("WhatsApp recovery failed safely", errors.getvalue())
