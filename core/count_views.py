import uuid

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from . import counts
from .models import Product, StockCount
from .views import protected


@protected("operate_inventory|approve_operations")
def index(request, branch):
    if request.method == "POST":
        count = counts.start_count(request.user, branch, request.POST.get("key"), request.POST.get("category", ""))
        return redirect("stock_count", pk=count.pk)
    return render(request, "counts.html", {
        "title": "Physical stock counts", "key": uuid.uuid4(),
        "categories": Product.objects.filter(active=True).exclude(category="").order_by("category").values_list("category", flat=True).distinct(),
        "rows": StockCount.objects.filter(branch=branch).select_related("created_by", "reviewed_by")[:100],
    })


@protected("operate_inventory|approve_operations")
def detail(request, branch, pk):
    count = get_object_or_404(StockCount, pk=pk, branch=branch)
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("save", "submit"):
            values = {str(line.pk): (request.POST.get("quantity_" + str(line.pk), ""),
                request.POST.get("reason_" + str(line.pk), "")) for line in count.lines.all()}
            counts.save_count(request.user, branch, pk, values, request.POST.get("note", ""), action == "submit")
        else:
            counts.review_count(request.user, branch, pk, action, request.POST.get("review_note", ""))
        messages.success(request, "Stock count recorded.")
        return redirect("stock_count", pk=pk)
    return render(request, "count.html", {"title": "Count sheet", "count": count,
        "lines": count.lines.select_related("product"),
        "editable": count.status == "draft" and count.created_by_id == request.user.pk,
        "can_review": count.status == "submitted" and count.created_by_id != request.user.pk and request.user.has_perm("core.approve_operations"),
    })
