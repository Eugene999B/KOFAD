"""A permission-scoped staff mobile API entry point.

This is deliberately browser-session authenticated for now. Native bearer
credentials are NOT issued until KOFAD has a reviewed mobile identity grant,
verified MFA binding, refresh-token rotation and logout revocation.
"""
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import Access, Branch
from .security import requires_mfa

MODULE_PERMISSIONS = {
    "sales": "core.operate_sales",
    "inventory": "core.operate_inventory",
    "finance": "core.operate_finance",
    "approvals": "core.approve_operations",
    "reports": "core.view_reports",
    "configuration": "core.manage_company",
}


def _private(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Vary"] = "Cookie"
    return response


@require_GET
def bootstrap(request):
    user = request.user
    if not user.is_authenticated or not user.is_active:
        return _private({"error": "authentication_required"}, 401)

    access = Access.objects.filter(user=user).first()
    if access is None or request.session.get("access_version") != access.session_version:
        return _private({"error": "reauthentication_required"}, 401)
    if access.force_password_change:
        return _private({"error": "password_change_required"}, 403)
    if settings.PRIVILEGED_MFA_ENFORCED and requires_mfa(user):
        try:
            mfa_ok = (float(request.session.get("mfa_verified_at")) +
                      settings.MFA_SESSION_SECONDS > timezone.now().timestamp())
        except (TypeError, ValueError):
            mfa_ok = False
        if not mfa_ok:
            return _private({"error": "mfa_required"}, 403)

    assigned = Branch.objects.filter(active=True)
    if not user.is_superuser:
        assigned = assigned.filter(access__user=user)
    branch = assigned.filter(pk=request.session.get("branch")).first()
    if branch is None:
        return _private({"error": "select_authorized_branch"}, 403)

    return _private({
        "version": 1,
        "channel": "staff",
        "branch": {"id": branch.pk, "name": branch.name},
        "permissions": {
            name: user.has_perm(perm)
            for name, perm in MODULE_PERMISSIONS.items()
        },
        "features": {
            "browser_session_authenticated": True,
            "native_mobile_token_login": False,
            "native_financial_mutations": False,
            "native_approval_mutations": False,
            "background_push": False,
        },
    })
