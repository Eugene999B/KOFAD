"""Owner-controlled salary recipient verification and batch release."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from core.models import Worker, PayrollPeriod
from core.views import branch_for
from . import payroll
from .models import PayrollTransferBatch
from .views import _finance_staff


@login_required
@require_POST
def verify_recipient(request, period_id, worker_id):
    branch = branch_for(request)
    _finance_staff(request, approval=True)
    get_object_or_404(PayrollPeriod, pk=period_id, branch=branch)
    worker = get_object_or_404(Worker, pk=worker_id, branch=branch)
    try:
        profile = payroll.save_recipient(
            request.user, worker,
            payment_type=request.POST.get("payment_type", ""),
            destination=request.POST.get("destination", ""),
            channel=request.POST.get("channel", ""),
            bank_code=request.POST.get("bank_code", ""),
        )
        messages.success(
            request, f"Hubtel confirmed the salary beneficiary name: {profile.provider_name} "
                     f"(ending {profile.destination_suffix}).",
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    return redirect("payroll_period", pk=period_id)


@login_required
@require_POST
def prepare(request, period_id):
    branch = branch_for(request)
    _finance_staff(request)
    period = get_object_or_404(PayrollPeriod, pk=period_id, branch=branch)
    try:
        batch = payroll.prepare_batch(request.user, period)
        messages.success(
            request, f"Hubtel payroll batch prepared: GHS {batch.expected_amount:.2f}. "
                     "A different manager must approve before any money can move.",
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    return redirect("payroll_period", pk=period_id)


@login_required
@require_POST
def approve(request, period_id):
    branch = branch_for(request)
    _finance_staff(request, approval=True)
    period = get_object_or_404(PayrollPeriod, pk=period_id, branch=branch)
    batch = get_object_or_404(PayrollTransferBatch, period=period)
    try:
        batch = payroll.approve_and_send(request.user, batch.pk)
        messages.success(
            request, "Approved payroll transfer batch submitted. Salary receipts are recorded "
                     "only as each Hubtel payout is independently confirmed.",
        )
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    return redirect("payroll_period", pk=period_id)
