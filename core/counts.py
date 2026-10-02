"""Blind physical counts with independent review and stale-snapshot protection."""
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from . import services as s
from .models import Movement, Product, Stock, StockCount, StockCountLine


def snapshots(branch, ids):
    quantities = dict(Stock.objects.filter(branch=branch, product_id__in=ids).values_list("product_id", "quantity"))
    movements = {r["product_id"]: r["last"] for r in Movement.objects.filter(
        branch=branch, product_id__in=ids).values("product_id").annotate(last=Max("pk"))}
    return quantities, movements


@transaction.atomic
def start_count(user, branch, key, category=""):
    s.permit(user, branch, "operate_inventory")
    s.lock_branch(branch)
    try:
        key = uuid.UUID(str(key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("A valid count request key is required.")
    category = str(category).strip()
    if len(category) > 80:
        raise ValidationError("Choose a valid category.")
    existing = StockCount.objects.filter(pk=key).first()
    if existing:
        if existing.branch_id != branch.pk or existing.created_by_id != user.pk or existing.scope != category:
            raise ValidationError("This request key belongs to a different count.")
        return existing
    products = Product.objects.filter(active=True)
    if category:
        products = products.filter(category=category)
    products = list(products[:501])
    if not products or len(products) > 500:
        raise ValidationError("Choose a category containing between 1 and 500 active products.")
    quantities, movements = snapshots(branch, [p.pk for p in products])
    count = StockCount.objects.create(id=key, branch=branch, scope=category, created_by=user)
    StockCountLine.objects.bulk_create([StockCountLine(count=count, product=p,
        expected=quantities.get(p.pk, 0), movement_id=movements.get(p.pk, 0)) for p in products])
    s.audit(user, branch, "count.started", count.pk, {"products": len(products), "category": category})
    return count


@transaction.atomic
def save_count(user, branch, count_id, values, note="", submit=False):
    s.permit(user, branch, "operate_inventory")
    s.lock_branch(branch)
    count = StockCount.objects.select_for_update().get(pk=count_id, branch=branch)
    if count.created_by_id != user.pk:
        raise PermissionDenied("Only the counter can enter these quantities.")
    if count.status != "draft":
        raise ValidationError("This count is no longer editable.")
    lines = list(count.lines.select_related("product"))
    if set(values) != {str(line.pk) for line in lines}:
        raise ValidationError("Submit exactly the products on this count sheet.")
    for line in lines:
        raw, reason = values[str(line.pk)]
        raw, reason = str(raw).strip(), str(reason).strip()
        if len(reason) > 240:
            raise ValidationError("Keep each discrepancy explanation within 240 characters.")
        if raw == "":
            line.counted = None
        elif not raw.isascii() or not raw.isdigit() or len(raw) > 10 or int(raw) > 2000000000:
            raise ValidationError("Counted quantities must be whole base units between zero and 2,000,000,000.")
        else:
            line.counted = int(raw)
        line.reason = reason
        if submit and line.counted is None:
            raise ValidationError("Enter every quantity, including zero, before submitting.")
        # Blind entry: require an observation for every line so validation never reveals expected stock.
        if submit and len(reason) < 5:
            raise ValidationError("Record an observation of at least five characters for every product.")
    if len(str(note)) > 2000:
        raise ValidationError("Keep the count note within 2,000 characters.")
    StockCountLine.objects.bulk_update(lines, ["counted", "reason"])
    count.note = str(note).strip()
    if submit:
        count.status = "submitted"
        count.submitted_at = timezone.now()
    count.save(update_fields=["note", "status", "submitted_at"])
    s.audit(user, branch, "count.submitted" if submit else "count.saved", count.pk)
    return count


@transaction.atomic
def review_count(user, branch, count_id, action, note=""):
    s.permit(user, branch, "operate_inventory" if action == "cancel" else "approve_operations")
    s.lock_branch(branch)
    count = StockCount.objects.select_for_update().get(pk=count_id, branch=branch)
    if action == "cancel":
        if count.created_by_id != user.pk:
            raise PermissionDenied("Only the counter can cancel this count.")
        if count.status not in ("draft", "submitted"):
            raise ValidationError("This count has already been completed.")
        count.status = "cancelled"
    else:
        if action not in ("approve", "reject") or count.status != "submitted":
            raise ValidationError("Choose a valid action for a submitted count.")
        if count.created_by_id == user.pk:
            raise ValidationError("A different authorized colleague must review this count.")
        note = str(note).strip()
        if len(note) < 5 or len(note) > 2000:
            raise ValidationError("Provide a review note between 5 and 2,000 characters.")
        if action == "approve":
            s.ensure_open(branch)
            lines = list(count.lines.select_related("product"))
            quantities, movements = snapshots(branch, [line.product_id for line in lines])
            if any(quantities.get(line.product_id, 0) != line.expected or
                   movements.get(line.product_id, 0) != line.movement_id for line in lines):
                raise ValidationError("Stock moved after this count started. Reject it and start a fresh count; no adjustments were posted.")
            if any(line.counted is None for line in lines):
                raise ValidationError("The submitted count is incomplete.")
            for line in lines:
                s.stock_move(user, branch, line.product, line.variance, str(count.pk),
                    "Approved physical count: " + line.reason)
            count.status = "approved"
        else:
            count.status = "rejected"
    count.reviewed_by = user
    count.reviewed_at = timezone.now()
    count.review_note = str(note).strip()[:2000]
    count.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
    s.audit(user, branch, "count." + count.status, count.pk, {"note": count.review_note})
    return count
