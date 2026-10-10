from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db.models import F
from .models import Access, Audit, Branch, CommunicationSettings, Company, DebtSettings, Document, Line, ManagementContact, Movement, Party, Payment, Product, Closing, Operation
from .models import QuarantineItem, StockCount, StockCountLine, SupplierReturn, TransferReceipt
from .mobile_release_models import MobileReleasePolicy, MobileNotice
from .services import audit

admin.site.site_header = "KOFAD administration"
admin.site.site_title = "KOFAD"
admin.site.index_title = "Access and configuration"


class AuditedAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit(request.user, None, "admin.saved", f"{obj._meta.label}:{obj.pk}", {"fields": form.changed_data})
    def delete_model(self, request, obj):
        audit(request.user, None, "admin.deleted", f"{obj._meta.label}:{obj.pk}")
        super().delete_model(request, obj)
    actions = None


class BranchAdmin(AuditedAdmin):
    list_display = ["name", "code", "active"]


@admin.action(description="Revoke selected users' sessions")
def revoke_sessions(modeladmin, request, queryset):
    for access in queryset:
        Access.objects.filter(pk=access.pk).update(session_version=F("session_version") + 1)
        audit(request.user, None, "access.revoked", access.user_id)


class AccessForm(forms.ModelForm):
    class Meta:
        model = Access
        fields = ["user", "branches", "recovery_phone"]
    def clean_recovery_phone(self):
        from .sms.service import normalize_phone
        raw = self.cleaned_data["recovery_phone"].strip()
        return normalize_phone(raw) if raw else ""


class AccessAdmin(AuditedAdmin):
    form = AccessForm
    list_display = ["user", "recovery_phone"]
    search_fields = ["user__username", "recovery_phone"]
    fields = ["user", "branches", "recovery_phone", "session_version"]
    readonly_fields = ["session_version"]
    filter_horizontal = ["branches"]
    actions = [revoke_sessions]
    def save_model(self, request, obj, form, change):
        if change:
            obj.session_version += 1
            from .models import PasswordRecovery
            PasswordRecovery.objects.filter(user_id=obj.user_id, used=False).update(used=True)
        super().save_model(request, obj, form, change)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


admin.site.unregister(User)
class UserAdmin(BaseUserAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        access, _ = Access.objects.get_or_create(user=obj)
        Access.objects.filter(pk=access.pk).update(session_version=F("session_version") + 1)
        audit(request.user, None, "user.updated", obj.pk, {"fields": form.changed_data})

admin.site.register(User, UserAdmin)
admin.site.register(Branch, BranchAdmin)
admin.site.register(Access, AccessAdmin)
# Business records are deliberately read-only in administration.
for model in [Company, Product, Party, Document, Line, Payment, Movement, Audit, Closing, Operation,
              StockCount, StockCountLine, TransferReceipt, SupplierReturn, QuarantineItem,
              DebtSettings, CommunicationSettings, ManagementContact]:
    admin.site.register(model, ReadOnlyAdmin)

# Only system administrators can change mobile release requirements or
# publish global announcements. Never use these for personal/staff data.
class MobileSuperuserAdmin(AuditedAdmin):
    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)
    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)
    def has_add_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)
    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MobileReleasePolicy)
class MobileReleasePolicyAdmin(MobileSuperuserAdmin):
    list_display = ("channel", "minimum_android_version", "updated_at")
    fields = ("channel", "minimum_android_version", "critical_update_reason")
    readonly_fields = ()
    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj)


@admin.register(MobileNotice)
class MobileNoticeAdmin(MobileSuperuserAdmin):
    list_display = ("channel", "kind", "title", "priority", "enabled", "created_at", "expires_at")
    list_filter = ("channel", "kind", "priority", "enabled")
    fields = ("channel", "kind", "title", "message", "priority", "enabled", "expires_at")
    search_fields = ("title", "message")
