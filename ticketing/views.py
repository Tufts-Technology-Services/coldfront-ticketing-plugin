import logging

import requests
from coldfront.core.allocation.models import Allocation
from coldfront.core.project.models import Project
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from ticketing import toggle
from ticketing.client import ServiceNowError
from ticketing.linking import LinkError, link_targets, link_ticket
from ticketing.permissions import is_ticketing_admin

logger = logging.getLogger(__name__)


class TicketingToggleView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Flip the acting ticketing admin's switch.

    htmx posts here and swaps in the re-rendered menu-bar control; a plain form post is redirected
    back to the page it came from.
    """

    def test_func(self):
        return is_ticketing_admin(self.request.user)

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


class LinkTicketView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Link an incident filed in ServiceNow to an allocation's or project's request (ticketing.linking).

    Posted from the ticket card's form; the outcome is shown as a message on the page it came from.
    """

    SUBJECTS = {
        "allocation": (Allocation, "allocation-detail"),
        "project": (Project, "project-detail"),
    }

    def test_func(self):
        return is_ticketing_admin(self.request.user)

    def post(self, request, subject_type, pk):
        if subject_type not in self.SUBJECTS:
            raise Http404(f"No tickets are linked to a {subject_type}")
        model, detail = self.SUBJECTS[subject_type]
        subject = get_object_or_404(model, pk=pk)
        back = redirect(reverse(detail, kwargs={"pk": subject.pk}))

        targets = {target.correlation_id: target for target in link_targets(subject)}
        target = targets.get(request.POST.get("target", ""))
        if target is None:
            messages.error(request, "Choose what the ServiceNow ticket is for.")
            return back
        number = request.POST.get("number", "")
        try:
            linked = link_ticket(number, target, request.user)
        except LinkError as e:
            messages.error(request, f"Not linked: {e}")
            return back
        except (ServiceNowError, requests.RequestException):
            logger.exception("Linking %r to %s failed", number, target.correlation_id)
            messages.error(request, "Not linked: ServiceNow couldn't be reached or refused the change. Try again.")
            return back

        message = f"Linked {linked.incident.number} to {target.label.lower()}."
        if linked.duplicate:
            message += f" ColdFront's {linked.duplicate.number} was closed as a duplicate of it."
        messages.success(request, message)
        return back
