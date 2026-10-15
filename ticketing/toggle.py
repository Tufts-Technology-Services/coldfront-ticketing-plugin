"""The per-admin switch that turns ticketing off for the admin's own actions.

The switch lives in the admin's session, so it ends with the session (logout, or the inactivity
timeout). Signal receivers aren't handed the request, so `enqueue_ticket` reads it from
`HistoricalRecords.context.request`, which simple-history's `HistoryRequestMiddleware` sets for the
length of every request. Without that middleware the switch could be turned off and do nothing, so
`tracking_active` checks, at request time, that the middleware has stashed this request.
"""

import logging

from simple_history.models import HistoricalRecords

from ticketing.permissions import is_ticketing_admin

logger = logging.getLogger(__name__)

SESSION_KEY = "ticketing_off"


def current_request():
    """The request being handled on this thread, or None outside a request."""
    return getattr(HistoricalRecords.context, "request", None)


def tracking_active(request):
    """True when HistoryRequestMiddleware has stashed this request, so the switch can take effect."""
    return current_request() is request


def is_off(request):
    """True when the request's user is a ticketing admin who has switched ticketing off."""
    return is_ticketing_admin(getattr(request, "user", None)) and bool(request.session.get(SESSION_KEY, False))


def set_off(request, off):
    request.session[SESSION_KEY] = off
    logger.info("Ticketing switched %s by %s", "off" if off else "on", request.user.username)


def state(request):
    """What the menu-bar control renders from."""
    return {"off": is_off(request), "available": tracking_active(request)}
