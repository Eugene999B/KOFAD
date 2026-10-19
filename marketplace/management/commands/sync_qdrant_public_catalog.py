"""Manually index approved public KOFAD products into a capped Qdrant collection."""
from django.core.management.base import BaseCommand, CommandError

from marketplace.qdrant_products import sync_public_catalog


class Command(BaseCommand):
    help = "Upsert at most 250 published Market listings into Qdrant Cloud; never changes KOFAD records."

    def handle(self, *args, **options):
        try:
            count = sync_public_catalog()
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS(f"Synced {count} published KOFAD product(s)."))
