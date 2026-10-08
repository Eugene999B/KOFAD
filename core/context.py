from django.conf import settings

from .models import Branch, Company


def shell(request):
    branches = Branch.objects.filter(active=True)
    if request.user.is_authenticated and not request.user.is_superuser:
        branches = branches.filter(access__user=request.user)
    if not request.user.is_authenticated:
        branches = Branch.objects.none()
    current = branches.filter(pk=request.session.get("branch")).first() or branches.first()
    online_order_attention = 0
    market_unread = 0
    if request.user.is_authenticated:
        from marketplace.models import ConversationMessage, OnlineOrder
        if current:
            online_order_attention = OnlineOrder.objects.filter(
                branch=current,
                status__in=["paid", "confirmed", "preparing", "ready_pickup", "out_for_delivery"],
            ).count()
        market_unread = ConversationMessage.objects.filter(
            read_by_staff=False
        ).exclude(sender_type="staff").count()
    return {
        "company": Company.objects.first() or Company(),
        "branches": branches,
        "current_branch": current,
        "online_order_attention": online_order_attention,
        "market_unread": market_unread,
        "staff_login_path": settings.STAFF_LOGIN_PATH,
    }
