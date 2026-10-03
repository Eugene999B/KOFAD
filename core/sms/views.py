from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .service import receive_callback, receive_delivery_callback


@require_GET
def callback(request, attempt_id):
    if len(request.META.get("QUERY_STRING", "")) > 2000:
        return JsonResponse({"ok": False}, status=400)
    accepted = receive_callback(
        attempt_id,
        request.GET.get("token", ""),
        request.GET.get("sms_id", ""),
        request.GET.get("status", ""),
    )
    return JsonResponse({"ok": accepted}, status=200 if accepted else 403)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def delivery_callback(request):
    if len(request.META.get("QUERY_STRING", "")) > 2000:
        return JsonResponse({"ok": False}, status=400)
    accepted = receive_delivery_callback(
        request.GET.get("token", ""),
        request.GET.get("sms_id", ""),
        request.GET.get("status", ""),
    )
    # Arkesel may deliver a callback before the provider id commit becomes visible.
    # Unknown ids are acknowledged safely; polling will reconcile them shortly.
    return JsonResponse({"ok": accepted}, status=200)
