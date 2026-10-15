from ticketing import toggle
from ticketing.permissions import is_ticketing_admin


def ticketing_toggle(request):
    """Give ticketing admins' pages the state of their ticketing switch, for the menu-bar control.

    Nobody else gets the variable, so `{% if ticketing_toggle %}` around the include hides the
    control from them, and from sites where this processor isn't configured.
    """
    if not is_ticketing_admin(getattr(request, "user", None)):
        return {}
    return {"ticketing_toggle": toggle.state(request)}
