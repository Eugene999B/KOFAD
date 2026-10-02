from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .service import receive_callback


@require_GET
def callback(request,attempt_id):
    if len(request.META.get("QUERY_STRING",""))>2000:
        return JsonResponse({"ok":False},status=400)
    accepted = receive_callback(attempt_id,request.GET.get("token",""),request.GET.get("sms_id",""),request.GET.get("status",""))
    return JsonResponse({"ok":accepted},status=200 if accepted else 403)
