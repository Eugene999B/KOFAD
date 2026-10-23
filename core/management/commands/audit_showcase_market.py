"""Read-only audit of synthetic-prefix products linked to live Market listings.

This command never edits, hides, reprices, publishes, unpublishes, or deletes
a product, stock row, document, payment, online order or accounting entry.
A SHOW- SKU is a review indicator, not a license to delete historical records.
"""
import json
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from core.models import Audit, Line, Movement, Product, Stock
from marketplace.models import MarketListing, OnlineOrderLine


class Command(BaseCommand):
    help = "Read-only inventory and ledger exposure report for SHOW- showcase records."

    def handle(self, *args, **options):
        products = list(Product.objects.filter(sku__istartswith="SHOW-").order_by("sku"))
        ids = [product.pk for product in products]
        listings = {
            listing.product_id: listing
            for listing in MarketListing.objects.select_related("product").filter(
                product_id__in=ids,
            )
        }
        stock = {
            row["product_id"]: int(row["units"] or 0)
            for row in Stock.objects.filter(product_id__in=ids).order_by()
            .values("product_id").annotate(units=Sum("quantity"))
        }

        def counts(model):
            from django.db.models import Count
            return {
                row["product_id"]: row["qty"]
                for row in model.objects.filter(product_id__in=ids).order_by()
                .values("product_id").annotate(qty=Count("pk"))
            }

        sale_lines = counts(Line)
        order_lines = counts(OnlineOrderLine)
        movements = counts(Movement)
        entries = []
        for product in products:
            listing = listings.get(product.pk)
            price = listing.market_price if listing else None
            factor = listing.factor if listing else 1
            cost_for_unit = product.cost * Decimal(factor)
            below_cost = bool(listing and price is not None and price < cost_for_unit)
            entries.append({
                "sku": product.sku,
                "product_name": product.name,
                "product_active": product.active,
                "market_enabled": bool(listing and listing.enabled),
                "market_price_source": listing.price_source if listing else None,
                "published_price_ghs": str(price) if price is not None else None,
                "recorded_cost_for_sale_unit_ghs": str(cost_for_unit),
                "listed_below_recorded_cost": below_cost,
                "stock_base_units": stock.get(product.pk, 0),
                "linked_sale_and_purchase_lines": sale_lines.get(product.pk, 0),
                "linked_online_order_lines": order_lines.get(product.pk, 0),
                "linked_stock_movements": movements.get(product.pk, 0),
            })
        markers = {
            str(row["reference"])
            for row in Audit.objects.filter(
                reference__in=["SHOWCASE-DATA-V1", "SHOWCASE-EXTENDED-V2-20261004"]
            ).values("reference").distinct()
        }
        self.stdout.write(json.dumps({
            "read_only": True,
            "warning": (
                "SHOW- prefixes and audit markers are review signals, not proof "
                "that an item is safe to delete. Reconcile original transactions "
                "and obtain owner/accountant approval before changing real data."
            ),
            "showcase_audit_markers_present": sorted(markers),
            "summary": {
                "show_prefix_products": len(entries),
                "enabled_market_listings": sum(x["market_enabled"] for x in entries),
                "enabled_below_cost_listings": sum(
                    x["market_enabled"] and x["listed_below_recorded_cost"]
                    for x in entries
                ),
                "linked_sale_and_purchase_lines": sum(
                    x["linked_sale_and_purchase_lines"] for x in entries
                ),
                "linked_online_order_lines": sum(
                    x["linked_online_order_lines"] for x in entries
                ),
            },
            "products": entries,
        }, indent=2, sort_keys=True))
