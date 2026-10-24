"""Inventory totals backed by authorized KOFAD stock records, not low-stock alerts."""
from django.db.models import Count, Sum

from .telegram_intelligence import _branches, _role


def inventory_totals(user):
    from .models import Stock
    if not _role(user)["inventory"]:
        return "Your role does not permit inventory reporting."
    branches = _branches(user)
    if not branches.exists():
        return "No authorized active branches are assigned."
    rows = Stock.objects.filter(branch__in=branches, product__active=True)
    with_stock = rows.filter(quantity__gt=0)
    product_types = with_stock.values("product_id").distinct().count()
    stocked_rows = rows.count()
    unique_recorded = rows.values("product_id").distinct().count()
    units = with_stock.aggregate(total=Sum("quantity"))["total"] or 0
    empty = rows.filter(quantity=0).count()
    return (
        f"{product_types:,} distinct product(s) have a positive recorded stock balance "
        f"across your {branches.count()} authorized branch(es). "
        f"They represent {units:,} individual base units in recorded stock. "
        f"There are {unique_recorded:,} distinct products with stock records "
        f"({stocked_rows:,} branch-product records), of which {empty:,} branch-product "
        f"record(s) have zero balance. "
        "A catalog entry alone is not evidence of stock in your branches. "
        "These are current recorded balances, not a physical stock count."
    )
