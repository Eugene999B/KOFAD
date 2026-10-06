from unittest.mock import patch

from django.test import SimpleTestCase

from core.management.commands.process_sms import Command


class CommunicationWorkerScheduleTests(SimpleTestCase):
    def test_receipts_stay_fast_while_stale_recovery_is_bounded(self):
        module = "core.management.commands.process_sms."
        with (
            patch(module + "time.monotonic", side_effect=range(100, 135, 5)),
            patch(module + "time.sleep", side_effect=[None] * 6 + [InterruptedError]),
            patch(module + "close_old_connections"),
            patch(module + "run_scheduled_automations") as automate,
            patch(module + "sync_delivery_reports", return_value=0) as sync,
            patch(module + "recover_stale") as recover,
            patch(module + "recover_stale_whatsapp") as recover_whatsapp,
        ):
            with self.assertRaises(InterruptedError):
                Command().handle(loop=True)
        self.assertEqual(sync.call_count, 7)
        self.assertEqual(recover.call_count, 2)
        self.assertEqual(recover_whatsapp.call_count, 2)
        self.assertEqual(automate.call_count, 1)

    def test_one_shot_still_recovers_stale_deliveries(self):
        module = "core.management.commands.process_sms."
        with (
            patch(module + "time.monotonic", return_value=100),
            patch(module + "close_old_connections"),
            patch(module + "run_scheduled_automations"),
            patch(module + "sync_delivery_reports", return_value=0),
            patch(module + "recover_stale") as recover,
            patch(module + "recover_stale_whatsapp") as recover_whatsapp,
        ):
            Command().handle(loop=False)
        recover.assert_called_once()
        recover_whatsapp.assert_called_once()
