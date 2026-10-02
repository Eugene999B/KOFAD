from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db.models import F
from .models import Access, Audit, Branch, Company, Document, Line, Movement, Party, Payment, Product, Closing, Operation
from .models import StockCount, StockCountLine, TransferReceipt
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
for model in [Company, Product, Party, Document, Line, Payment, Movement, Audit, Closing, Operation, StockCount, StockCountLine, TransferReceipt]:
    admin.site.register(model, ReadOnlyAdmin)
