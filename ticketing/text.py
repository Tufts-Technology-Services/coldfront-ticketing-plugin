"""Rendering the text tickets carry.

Every ticket template is rendered through render_ticket_text, which adds what all of them may use:

    site_name   what customers and admins call this ColdFront (TICKETING_SITE_NAME, default
                "ColdFront"), so the generic sentences follow one setting rather than an edit to
                each template. Site-specific prose (partials/rcrm.txt) is changed by overriding it.
"""

from django.conf import settings
from django.template.loader import render_to_string

DEFAULT_SITE_NAME = "ColdFront"


def site_name() -> str:
    """What tickets call this ColdFront: TICKETING_SITE_NAME, else "ColdFront"."""
    return getattr(settings, "TICKETING_SITE_NAME", DEFAULT_SITE_NAME)


def render_ticket_text(template_name: str | list[str], context: dict | None = None) -> str:
    """render_to_string, with the context every ticket template may use (see the module docstring)."""
    return render_to_string(template_name, {"site_name": site_name(), **(context or {})})
