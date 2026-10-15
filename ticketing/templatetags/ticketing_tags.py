from dataclasses import dataclass

from django import template
from django.urls import reverse

from ticketing.linking import allocation_label, change_label, link_targets
from ticketing.models import FiledTicket
from ticketing.permissions import is_ticketing_admin
from ticketing.slots import STORAGE_FAILURE_SUFFIX

register = template.Library()


@dataclass(frozen=True)
class TicketRow:
    """A row of the ticket card: the ticket, what it's about, and for a storage failure its parent."""

    ticket: FiledTicket
    about: str  # e.g. "Research Storage allocation (New, 2026-10-14)"
    about_url: str | None  # the page for what it's about, if ColdFront has one
    parent: FiledTicket | None  # a storage failure's request ticket, if there is one


def _is_ticketing_admin(context):
    return is_ticketing_admin(getattr(context.get("request"), "user", None))


@register.simple_tag(takes_context=True)
def filed_tickets_for(context, subject):
    """TicketRows for the tickets filed about `subject` (an allocation, project or user), newest
    first; empty for anyone but a ticketing admin."""
    if not _is_ticketing_admin(context):
        return []
    tickets = list(
        FiledTicket.objects.about(subject).select_related(
            "allocation__status", "allocation_change__status", "project", "user", "linked_by"
        )
    )
    # A storage failure's request ticket has the same subjects, so it's among these already.
    by_correlation_id = {t.correlation_id: t for t in tickets}
    resources = {}  # allocation pk -> parent resource, so each is looked up once
    return [_row(ticket, by_correlation_id, resources) for ticket in tickets]


def _row(ticket, by_correlation_id, resources) -> TicketRow:
    def about_allocation(allocation):
        if allocation.pk not in resources:
            resources[allocation.pk] = allocation.get_parent_resource
        return allocation_label(allocation, resources[allocation.pk])

    parent = None
    if ticket.kind == FiledTicket.Kind.STORAGE_FAILURE:
        parent = by_correlation_id.get(ticket.correlation_id.removesuffix(STORAGE_FAILURE_SUFFIX))
    if ticket.allocation_change:
        about = change_label(ticket.allocation_change)
        if ticket.allocation:
            about += f" to {about_allocation(ticket.allocation)}"
        url = ticket.allocation_change.get_absolute_url()
    elif ticket.allocation:
        about, url = about_allocation(ticket.allocation), ticket.allocation.get_absolute_url()
    elif ticket.project:
        # A project user notice's correlation_id ends with the day it covers.
        day = ticket.correlation_id.rsplit(":", 1)[-1]
        about, url = f"{ticket.project.title}, {day}", ticket.project.get_absolute_url()
    elif ticket.user:
        about, url = ticket.user.username, None
    else:
        about, url = "(deleted from ColdFront)", None
    return TicketRow(ticket, about, url, parent)


@register.simple_tag(takes_context=True)
def link_form_for(context, subject):
    """Where the "Link ServiceNow ticket" form posts, and what it can link to; None for others."""
    if not _is_ticketing_admin(context):
        return None
    subject_type = type(subject).__name__.lower()
    return {
        "action": reverse("ticketing-link", kwargs={"subject_type": subject_type, "pk": subject.pk}),
        "targets": link_targets(subject),
    }
