from django.urls import path
from . import views

urlpatterns = [
    path("api/pos/hubtel/wallet-name/", views.staff_wallet_name, name="hubtel_direct_wallet_name"),
    path("internal/hubtel-direct/reconcile/", views.internal_reconcile, name="hubtel_direct_internal_reconcile"),
    path("market/orders/<uuid:order_id>/hubtel-direct/", views.collect, name="hubtel_direct_collect"),
    path("market/orders/<uuid:order_id>/hubtel-direct/verify-phone/", views.verify_registered_phone, name="hubtel_direct_verify_phone"),
    path("market/orders/<uuid:order_id>/hubtel-direct/status/", views.collect_status, name="hubtel_direct_collect_status"),
    path("market/payments/hubtel-direct/callback/", views.callback, name="hubtel_direct_callback"),
    path("finance/hubtel-direct/", views.finance_dashboard, name="hubtel_direct_finance"),
    path("finance/hubtel-direct/payouts/", views.payout, name="hubtel_direct_payout"),
    path("finance/hubtel-direct/payouts/<uuid:tx_id>/approve/", views.payout_approve, name="hubtel_direct_approve"),
    path("finance/hubtel-direct/payouts/<uuid:tx_id>/", views.payout_detail, name="hubtel_direct_payout_detail"),
]
