from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, Permission, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from . import services as s
from .models import Access, Branch, PasswordRecovery
from .sms.service import normalize_phone

ROLE_PERMISSION_CODES = [
    "operate_sales", "operate_inventory", "operate_finance", "approve_operations",
    "view_reports", "manage_company", "send_messages",
    "add_product", "change_product", "add_party", "change_party",
]

PERMISSION_HELP = {
    "operate_sales": ("Sales", "Create sales, use the counter and work with customer-facing sales records."),
    "operate_inventory": ("Inventory", "Receive purchases, manage inventory, Supplier Returns and Inventory Verification."),
    "operate_finance": ("Finance", "Record expenses, customer collections, supplier payments and daily closing."),
    "approve_operations": ("Approvals", "Review controlled returns, inventory verification, accounting, payroll, corrections and closing verification."),
    "view_reports": ("Reports", "View business reports, cost/profit information and audit evidence."),
    "manage_company": ("Company settings", "Manage business configuration. Only the system administrator can change staff access and roles."),
    "send_messages": ("Communications", "Queue and retry approved customer SMS messages."),
    "add_product": ("Products · create", "Create new products."),
    "change_product": ("Products · edit", "Edit product setup and selling prices."),
    "add_party": ("Contacts · create", "Create customers and suppliers where the role also has the relevant business access."),
    "change_party": ("Contacts · edit", "Edit customers and suppliers where the role also has the relevant business access."),
}

ROLE_DESCRIPTIONS = {
    "Owner": "Full business control, reporting, administration and approvals.",
    "Manager": "Daily operational control across sales, stock, finance, reports and approvals.",
    "Cashier": "Fast counter sales and quick customer creation.",
    "Storekeeper": "Purchasing, inventory, Supplier Returns and Inventory Verification.",
    "Accountant": "Finance, receivables/payables and reporting.",
    "Auditor": "Read-only business reporting and audit visibility.",
}


def _admin_branch(request):
    branches = Branch.objects.filter(active=True)
    if not request.user.is_superuser:
        branches = branches.filter(access__user=request.user)
    branch = branches.first()
    if not branch:
        raise ValidationError("No active business location is available.")
    return branch


def company_admin(view):
    @login_required
    @wraps(view)
    def inner(request, *args, **kwargs):
        branch = _admin_branch(request)
        s.permit(request.user, branch, "manage_company")
        return view(request, branch, *args, **kwargs)
    return inner


def _roles():
    return Group.objects.order_by("name")


def _role_permissions():
    return Permission.objects.filter(
        content_type__app_label="core", codename__in=ROLE_PERMISSION_CODES
    ).order_by("codename")


def _assigned_role(user):
    return user.groups.order_by("name").first()


@company_admin
def administration(request, branch):
    users = User.objects.order_by("-is_active", "username")
    active = users.filter(is_active=True).count()
    disabled = users.filter(is_active=False).count()
    roles = Group.objects.count()
    return render(request, "administration.html", {
        "title": "Administration",
        "users": users[:8],
        "active_users": active,
        "disabled_users": disabled,
        "role_count": roles,
        "branch_count": Branch.objects.filter(active=True).count(),
    })


@company_admin
def users(request, branch):
    rows = []
    for user in User.objects.prefetch_related("groups").select_related("access").order_by("-is_active", "username"):
        rows.append({
            "user": user,
            "role": "System administrator" if user.is_superuser else (_assigned_role(user).name if _assigned_role(user) else "No role"),
            "phone": user.access.recovery_phone,
        })
    return render(request, "admin_users.html", {"title": "Staff & users", "rows": rows})


@company_admin
def user_edit(request, branch, pk=None):
    if not request.user.is_superuser:
        raise PermissionDenied("Only a system administrator can change staff access.")
    user = get_object_or_404(User, pk=pk) if pk else None
    creating = user is None
    active_branches = list(Branch.objects.filter(active=True).order_by("name"))
    selected_branches = set(user.access.branches.values_list("pk", flat=True)) if user else ({branch.pk} if len(active_branches) == 1 else set())
    selected_role = _assigned_role(user).pk if user and not user.is_superuser and _assigned_role(user) else ""

    permissions = list(_role_permissions())
    extra_permissions = set(user.user_permissions.values_list("pk", flat=True)) if user else set()
    values = {
        "username": user.username if user else "",
        "first_name": user.first_name if user else "",
        "last_name": user.last_name if user else "",
        "recovery_phone": user.access.recovery_phone if user else "",
        "role": str(selected_role),
        "active": True if creating else user.is_active,
        "branches": selected_branches,
    }

    if request.method == "POST":
        values.update({
            "username": request.POST.get("username", "").strip()[:150],
            "first_name": request.POST.get("first_name", "").strip()[:150],
            "last_name": request.POST.get("last_name", "").strip()[:150],
            "recovery_phone": request.POST.get("recovery_phone", "").strip()[:40],
            "role": request.POST.get("role", ""),
            "active": request.POST.get("active") == "on",
            "branches": {int(x) for x in request.POST.getlist("branches") if x.isdigit()},
        })
        extra_raw = request.POST.getlist("extra_permissions")
        extra_permissions = {int(value) for value in extra_raw if value.isdigit()}
        errors = []
        if len(extra_permissions) != len(extra_raw) or not extra_permissions.issubset({p.pk for p in permissions}):
            errors.append("Choose valid additional permissions.")
        valid_branches = {b.pk for b in active_branches}
        if not values["branches"].issubset(valid_branches):
            errors.append("Choose valid active locations.")
        if not values["username"]:
            errors.append("Username is required.")
        duplicate = User.objects.filter(username__iexact=values["username"])
        if user:
            duplicate = duplicate.exclude(pk=user.pk)
        if duplicate.exists():
            errors.append("That username is already in use. Usernames are matched without case differences.")

        role = None
        if not (user and user.is_superuser):
            role = Group.objects.filter(pk=values["role"]).first() if values["role"].isdigit() else None
            if not role:
                errors.append("Choose a staff role.")

        try:
            phone = normalize_phone(values["recovery_phone"]) if values["recovery_phone"] else ""
        except ValidationError as exc:
            phone = ""
            errors.extend(exc.messages)

        password = request.POST.get("password", "")
        if creating and not password:
            errors.append("Set an initial password for the new staff account.")
        if password:
            candidate = user or User(username=values["username"], first_name=values["first_name"], last_name=values["last_name"])
            try:
                validate_password(password, candidate)
            except ValidationError as exc:
                errors.extend(exc.messages)

        if len(active_branches) > 1 and not values["branches"]:
            errors.append("Assign the user to at least one store or location.")
        if user and user.pk == request.user.pk and not values["active"]:
            errors.append("You cannot disable the account you are currently using.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                before = {}
                if user:
                    before = {
                        "username": user.username,
                        "name": user.get_full_name(),
                        "active": user.is_active,
                        "role": _assigned_role(user).name if _assigned_role(user) else "",
                        "recovery_phone": user.access.recovery_phone,
                        "additional_permissions": list(user.user_permissions.values_list("codename", flat=True)),
                    }
                if creating:
                    user = User.objects.create_user(username=values["username"], password=password)
                user.username = values["username"]
                user.first_name = values["first_name"]
                user.last_name = values["last_name"]
                user.is_active = values["active"]
                user.save()
                if password and not creating:
                    user.set_password(password)
                    user.save(update_fields=["password"])
                access, _ = Access.objects.get_or_create(user=user)
                phone_changed = access.recovery_phone != phone
                access.recovery_phone = phone
                access.save(update_fields=["recovery_phone"])
                if phone_changed:
                    PasswordRecovery.objects.filter(user=user, used=False).update(used=True)
                if not user.is_superuser:
                    if len(active_branches) == 1:
                        access.branches.set(active_branches)
                    else:
                        access.branches.set(Branch.objects.filter(active=True, pk__in=values["branches"]))
                    user.groups.set([role])
                    user.user_permissions.set(Permission.objects.filter(pk__in=extra_permissions))
                after = {
                    "username": user.username,
                    "name": user.get_full_name(),
                    "active": user.is_active,
                    "role": "System administrator" if user.is_superuser else role.name,
                    "recovery_phone": phone,
                    "additional_permissions": list(user.user_permissions.values_list("codename", flat=True)),
                }
                s.audit(request.user, branch, "staff.created" if creating else "staff.updated", user.pk, {
                    "before": before, "after": after, "password_reset": bool(password and not creating)
                })
            messages.success(request, "Staff account created." if creating else "Staff account updated.")
            return redirect("admin_users")

    return render(request, "admin_user_form.html", {
        "title": "New staff account" if creating else "Edit staff account",
        "edited_user": user,
        "creating": creating,
        "roles": _roles(),
        "active_branches": active_branches,
        "values": values,
        "single_branch": len(active_branches) == 1,
        "extra_choices": [{"id": p.pk, "label": PERMISSION_HELP.get(p.codename, (p.name, ""))[0],
                           "help": PERMISSION_HELP.get(p.codename, (p.name, ""))[1],
                           "checked": p.pk in extra_permissions} for p in permissions],
    })


@company_admin
def roles(request, branch):
    rows = []
    for role in _roles().prefetch_related("permissions"):
        rows.append({
            "role": role,
            "description": ROLE_DESCRIPTIONS.get(role.name, "Custom KOFAD role."),
            "permissions": [PERMISSION_HELP.get(p.codename, (p.name, ""))[0] for p in role.permissions.filter(content_type__app_label="core")],
            "members": role.user_set.count(),
        })
    return render(request, "admin_roles.html", {"title": "Roles & permissions", "rows": rows})


@company_admin
def role_edit(request, branch, pk=None):
    if not request.user.is_superuser:
        raise PermissionDenied("Only a system administrator can change roles.")
    role = get_object_or_404(Group, pk=pk) if pk else None
    creating = role is None
    permissions = list(_role_permissions())
    selected = set(role.permissions.filter(pk__in=[p.pk for p in permissions]).values_list("pk", flat=True)) if role else set()
    name = role.name if role else ""

    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:150]
        selected = {int(x) for x in request.POST.getlist("permissions") if x.isdigit()}
        errors = []
        if not name:
            errors.append("Role name is required.")
        duplicate = Group.objects.filter(name__iexact=name)
        if role:
            duplicate = duplicate.exclude(pk=role.pk)
        if duplicate.exists():
            errors.append("A role with that name already exists.")
        valid_ids = {p.pk for p in permissions}
        if not selected.issubset(valid_ids):
            errors.append("One or more selected permissions are invalid.")
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                before = {"name": role.name, "permissions": list(role.permissions.values_list("codename", flat=True))} if role else {}
                if creating:
                    role = Group.objects.create(name=name)
                else:
                    role.name = name
                    role.save(update_fields=["name"])
                role.permissions.set(Permission.objects.filter(pk__in=selected))
                after = {"name": role.name, "permissions": list(role.permissions.values_list("codename", flat=True))}
                s.audit(request.user, branch, "role.created" if creating else "role.updated", role.pk, {"before": before, "after": after})
            messages.success(request, "Role saved.")
            return redirect("admin_roles")

    choices = [{
        "permission": permission,
        "label": PERMISSION_HELP.get(permission.codename, (permission.name, ""))[0],
        "help": PERMISSION_HELP.get(permission.codename, (permission.name, ""))[1],
        "checked": permission.pk in selected,
    } for permission in permissions]
    return render(request, "admin_role_form.html", {
        "title": "New role" if creating else "Edit role",
        "role": role,
        "name": name,
        "choices": choices,
    })


@company_admin
def settings_center(request, branch):
    return render(request, "settings_center.html", {
        "title": "Settings",
        "single_branch": Branch.objects.filter(active=True).count() == 1,
    })
