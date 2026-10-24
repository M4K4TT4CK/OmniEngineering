"""CWE-352: a Django transfer endpoint behind the CSRF middleware."""
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_POST


@login_required
@require_POST
def transfer(request: HttpRequest) -> JsonResponse:
    amount = int(request.POST["amount"])
    request.user.account.transfer_to(request.POST["to"], amount)
    return JsonResponse({"ok": True})
