import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from ticketing import toggle

logger = logging.getLogger(__name__)


class TicketingToggleView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Flip the acting superuser's ticketing switch.

    htmx posts here and swaps in the re-rendered menu-bar control; a plain form post is redirected
    back to the page it came from.
    """

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request):
        if toggle.tracking_active(request):
            toggle.set_off(request, not toggle.is_off(request))
        else:
            # The switch would be stored and then ignored by enqueue_ticket, so refuse to set it.
            logger.error(
                "Not switching ticketing for %s: simple_history's HistoryRequestMiddleware is not active",
                request.user.username,
            )

        if request.headers.get("HX-Request") == "true":
            return render(request, "ticketing/navbar_toggle.html", {"ticketing_toggle": toggle.state(request)})
        back = request.headers.get("Referer")
        if back and url_has_allowed_host_and_scheme(back, {request.get_host()}, request.is_secure()):
            return redirect(back)
        return redirect("home")
