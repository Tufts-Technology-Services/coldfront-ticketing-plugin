from django import template
from django.urls import reverse

from ticketing.linking import link_targets
from ticketing.models import FiledTicket

register = template.Library()


def _is_superuser(context):
    user = getattr(context.get("request"), "user", None)
    return bool(user and user.is_superuser)


@register.simple_tag(takes_context=True)
def filed_tickets_for(context, subject):
    """The tickets filed about `subject` (an allocation, project or user), for superusers only."""
    if not _is_superuser(context):
        return FiledTicket.objects.none()
    return FiledTicket.objects.about(subject)


@register.simple_tag(takes_context=True)
def link_form_for(context, subject):
    """Where the "Link ServiceNow ticket" form posts, and what it can link to; None for non-superusers."""
    if not _is_superuser(context):
        return None
    subject_type = type(subject).__name__.lower()
    return {
        "action": reverse("ticketing-link", kwargs={"subject_type": subject_type, "pk": subject.pk}),
        "targets": link_targets(subject),
    }
