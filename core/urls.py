from django.urls import path
from . import views as v
from . import account as accounts
from . import count_views
from .sms.views import callback
urlpatterns = [
    path("account/", accounts.account, name="account"),
    path("forgot-password/", accounts.forgot_password, name="forgot_password"),
    path("forgot-password/code/", accounts.reset_password, name="reset_password"),
    path("stock-counts/", count_views.index, name="stock_counts"),
    path("stock-counts/<uuid:pk>/", count_views.detail, name="stock_count"),
    path("sms/callback/<uuid:attempt_id>/", callback, name="sms_callback"),
    path("message-templates/", v.message_templates, name="message_templates"),
    path("account/password/", v.password_change, name="password_change"),
    path("search/", v.search, name="search"),
    path("health/", v.health, name="health"), path("login/", v.login_view, name="login"),
    path("logout/", v.logout_view, name="logout"), path("mfa/", v.mfa, name="mfa"),
    path("branch/", v.switch_branch, name="switch_branch"), path("", v.dashboard, name="dashboard"),
    path("sales/new/", v.pos, name="pos"), path("purchasing/", v.purchasing, name="purchasing"),
    path("api/trades/", v.complete_trade), path("api/held/", v.hold),
    path("api/held/<int:pk>/", v.held),
    path("documents/", v.documents, name="documents"), path("documents/<uuid:pk>/", v.document, name="document"),
    path("inventory/", v.inventory, name="inventory"), path("products/new/", v.product_edit, name="product_new"),
    path("products/<int:pk>/", v.product_edit, name="product_edit"),
    path("parties/", v.parties, name="parties"), path("parties/new/", v.party_edit, name="party_new"),
    path("parties/<int:pk>/", v.party_edit, name="party_edit"), path("parties/<int:pk>/statement/", v.statement, name="statement"),
    path("corrections/", v.corrections, name="corrections"),
    path("finance/", v.finance, name="finance"), path("returns/", v.returns, name="returns"),
    path("operations/", v.operations, name="operations"), path("closings/", v.closings, name="closings"),
    path("reports/", v.reports, name="reports"), path("reports/export/<str:format>/", v.export_report, name="export"),
    path("audit/", v.audit_log, name="audit"), path("settings/", v.settings_view, name="settings"),
    path("communications/", v.communications, name="communications"),
]
