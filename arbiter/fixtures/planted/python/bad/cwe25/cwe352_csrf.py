"""CWE-352: a Django transfer endpoint with CSRF protection switched off."""
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


@csrf_exempt  # expect: arbiter/python.csrf-protection-disabled expect-ext: semgrep/semgrep-rules.python.django.security.audit.no-csrf-exempt
@require_POST
def transfer(request: HttpRequest) -> JsonResponse:
    amount = int(request.POST["amount"])
    request.user.account.transfer_to(request.POST["to"], amount)
    return JsonResponse({"ok": True})
