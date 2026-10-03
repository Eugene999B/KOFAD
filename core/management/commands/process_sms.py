import time
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from core.sms.service import process_one, recover_stale
from core.automations import run_scheduled_automations


class Command(BaseCommand):
    help = "Run the durable SMS outbox. Use --loop in a separate Railway worker."
    def add_arguments(self,parser):
        parser.add_argument("--loop",action="store_true")
        parser.add_argument("--limit",type=int,default=100)
    def handle(self,*args,**options):
        processed = 0
        last_automation = 0.0
        while True:
            close_old_connections()
            now = time.monotonic()
            if now - last_automation >= 60:
                try:
                    run_scheduled_automations()
                except Exception as exc:
                    self.stderr.write(f"Communication automation check failed safely: {exc}")
                last_automation = now
            try:
                recover_stale()
                found = process_one()
            except ValidationError as exc:
                raise CommandError("; ".join(exc.messages))
            if found:
                processed += 1
            if not options["loop"] and (not found or processed >= options["limit"]):
                break
            if not found:
                time.sleep(1)
        self.stdout.write(f"Processed {processed} queued SMS records.")
