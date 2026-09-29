from ticketing import toggle


def ticketing_toggle(request):
    """Give superusers' pages the state of their ticketing switch, for the menu-bar control.

    Nobody else gets the variable, so `{% if ticketing_toggle %}` around the include hides the
    control from them, and from sites where this processor isn't configured.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_superuser):
        return {}
    return {"ticketing_toggle": toggle.state(request)}
