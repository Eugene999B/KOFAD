from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="public_home"),
    path("market/", views.market, name="market"),
    path("market/products/<int:pk>/", views.product_detail, name="market_product"),
    path("market/products/<int:pk>/image/<str:size>/", views.product_image, name="market_product_image"),
    path("market/account/register/", views.account_start, name="market_register"),
    path("market/account/verify/", views.account_verify, name="market_verify"),
    path("market/account/finish/", views.account_finish, name="market_finish"),
    path("market/account/login/", views.customer_login, name="market_login"),
    path("market/account/logout/", views.customer_logout, name="market_logout"),
    path("market/cart/", views.cart, name="market_cart"),
    path("market/cart/add/<int:pk>/", views.cart_add, name="market_cart_add"),
    path("market/cart/update/", views.cart_update, name="market_cart_update"),
    path("market/checkout/", views.checkout, name="market_checkout"),
    path("market/orders/", views.customer_orders, name="market_orders"),
    path("market/orders/<uuid:pk>/", views.customer_order, name="market_order"),
    path("market/orders/<uuid:pk>/pay/", views.order_pay, name="market_order_pay"),
    path("market/messages/", views.customer_messages, name="market_messages"),
    path("market/messages/<int:conversation_id>/", views.customer_messages, name="market_message_thread"),
    path("market/payment/return/", views.payment_return, name="market_payment_return"),
    path("market/payments/paystack/webhook/", views.paystack_webhook, name="paystack_webhook"),
    path("online-orders/", views.staff_orders, name="staff_online_orders"),
    path("online-orders/<uuid:pk>/", views.staff_order, name="staff_online_order"),
    path("online-inbox/", views.staff_inbox, name="staff_market_inbox"),
    path("online-inbox/<int:conversation_id>/", views.staff_inbox, name="staff_market_thread"),
]
