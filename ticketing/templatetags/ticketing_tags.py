from dataclasses import dataclass

from django import template
from django.urls import reverse

from ticketing.linking import allocation_label, change_label, link_targets
from ticketing.models import FiledTicket
from ticketing.permissions import is_ticketing_admin
from ticketing.tasks import STORAGE_FAILURE_SUFFIX

register = template.Library()


@dataclass(frozen=True)
class TicketRow:
    """A row of the ticket card: the ticket, what it's about, and for a storage failure its parent."""

    ticket: FiledTicket
    about: str  # e.g. "Research Storage allocation (New, 2026-10-14)"
    about_url: str | None  # the page for what it's about, if ColdFront has one
    parent: FiledTicket | None = None  # a storage failure's request ticket, if there is one


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
    request_ids = [
        t.correlation_id.removesuffix(STORAGE_FAILURE_SUFFIX)
        for t in tickets
        if t.kind == FiledTicket.Kind.STORAGE_FAILURE
    ]
    parents = {t.correlation_id: t for t in FiledTicket.objects.filter(correlation_id__in=request_ids)}
    resources = {}  # allocation pk -> parent resource, so each is looked up once
    return [_row(ticket, parents, resources) for ticket in tickets]


def _row(ticket, parents, resources) -> TicketRow:
    def about_allocation(allocation):
        if allocation.pk not in resources:
            resources[allocation.pk] = allocation.get_parent_resource
        return allocation_label(allocation, resources[allocation.pk])

    parent = None
    if ticket.kind == FiledTicket.Kind.STORAGE_FAILURE:
        parent = parents.get(ticket.correlation_id.removesuffix(STORAGE_FAILURE_SUFFIX))
    if ticket.allocation_change:
        change = ticket.allocation_change
        about = change_label(change)
        if ticket.allocation:
            about += f" to {about_allocation(ticket.allocation)}"
        return TicketRow(ticket, about, reverse("allocation-change-detail", kwargs={"pk": change.pk}), parent)
    if ticket.allocation:
        url = reverse("allocation-detail", kwargs={"pk": ticket.allocation.pk})
        return TicketRow(ticket, about_allocation(ticket.allocation), url, parent)
    if ticket.project:
        # A project user notice's correlation_id ends with the day it covers.
        day = ticket.correlation_id.rsplit(":", 1)[-1]
        url = reverse("project-detail", kwargs={"pk": ticket.project.pk})
        return TicketRow(ticket, f"{ticket.project.title}, {day}", url)
    if ticket.user:
        return TicketRow(ticket, ticket.user.username, None)
    return TicketRow(ticket, "(deleted from ColdFront)", None, parent)


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
