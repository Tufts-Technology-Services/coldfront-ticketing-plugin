"""Adopting a ticket filed in ServiceNow directly, so ColdFront's resolution lands on it.

Some requests reach ServiceNow first (email, the portal) and an admin then makes the change in
ColdFront. From the ticket card, the admin links the incident to what ColdFront knows the request
as -- an allocation request, a change request, or today's project user changes. Linking gives the
incident that request's correlation_id and records it as a FiledTicket, so the tasks treat it as
their own: the create task finds it and files nothing, and the resolve task resolves it with the
customer message, leaving its caller and watch list alone.

If ColdFront already opened an incident for the request (the customer also submitted it in
ColdFront, so allocation_new filed one), that one is closed as a duplicate of the linked incident, the
way Tufts staff close duplicates: u_duplicate_of, state Closed, close notes "Duplicate ticket", no
close code. Its correlation_id is cleared first, so two incidents never share one.

A link can be undone (unlink_ticket) until the ticket is resolved.

Runs in the admin's request rather than a task, so they see at once whether it worked. It is an
explicit act, so the admin ticketing switch doesn't apply.
"""

import logging
import re
from dataclasses import dataclass, field

from coldfront.core.allocation.models import Allocation
from coldfront.core.project.models import Project
from django.utils import timezone

from ticketing.allocation_updates import resource_label
from ticketing.client import Incident, client_from_settings
from ticketing.models import FiledTicket
from ticketing.tasks import (
    DONE_STATES,
    STATE_CLOSED,
    allocation_correlation_id,
    change_correlation_id,
    default_assignment,
    project_users_correlation_id,
)

logger = logging.getLogger(__name__)

INCIDENT_NUMBER = re.compile(r"^INC\d+$")
DUPLICATE_CLOSE_NOTES = "Duplicate ticket"
# The link form's group for a project's own targets (today's user changes).
PROJECT_GROUP = "This project"
# Allocations a project page's link form leaves out: nothing will be resolved for them. Their own
# allocation pages still offer them.
HIDDEN_FROM_PROJECT = ("Denied", "Expired", "Revoked")


class LinkError(Exception):
    """Linking was refused; the message is for the admin."""


@dataclass(frozen=True)
class LinkTarget:
    """A request an incident can be linked to."""

    correlation_id: str
    label: str
    kind: str
    subjects: dict = field(compare=False)
    group: str = field(default="", compare=False)  # an <optgroup> in the form; "" for none
    of: str = field(default="", compare=False)  # its allocation, when offered on a project's page

    @property
    def description(self) -> str:
        """The target in a sentence, e.g. "the allocation request of the RStore allocation (...)"."""
        return self.label.lower() + (f" of the {self.of}" if self.of else "")


@dataclass(frozen=True)
class Linked:
    incident: Incident
    duplicate: Incident | None  # ColdFront's own incident, closed as a duplicate


def allocation_label(allocation, resource=None) -> str:
    """How an allocation is named on the ticket card: resource, status and when it was requested."""
    name = resource_label(allocation, resource or allocation.get_parent_resource)
    return f"{name} allocation ({allocation.status.name}, {allocation.created:%Y-%m-%d})"


def change_label(change) -> str:
    """How a change request is named on the ticket card."""
    return f"Change request {change.pk} ({change.status.name}, {change.created:%Y-%m-%d})"


def link_targets(subject) -> list[LinkTarget]:
    """What an incident can be linked to from `subject`'s page, most likely first.

    An allocation: its request, then its change requests, newest first. A project: today's user
    changes, so the change an admin is about to make resolves the linked incident; then each of its
    allocations' request and change requests, newest allocation first, grouped by allocation, less
    allocations with a status in HIDDEN_FROM_PROJECT.
    """
    if isinstance(subject, Allocation):
        return _allocation_targets(subject)
    if isinstance(subject, Project):
        targets = [
            LinkTarget(
                project_users_correlation_id(subject.pk),
                f"Today's user changes ({timezone.localdate():%Y-%m-%d})",
                FiledTicket.Kind.PROJECT_USERS,
                {"project": subject},
                group=PROJECT_GROUP,
            )
        ]
        allocations = subject.allocation_set.exclude(status__name__in=HIDDEN_FROM_PROJECT)
        for allocation in allocations.select_related("status").order_by("-created", "-pk"):
            targets += _allocation_targets(allocation, of=allocation_label(allocation))
        return targets
    raise TypeError(f"Nothing to link a ticket to on a {type(subject).__name__}")


def _allocation_targets(allocation, of="") -> list[LinkTarget]:
    """The allocation's request and change requests; `of` names the allocation on a project's page,
    where each allocation's targets are grouped under it."""
    targets = [
        LinkTarget(
            allocation_correlation_id(allocation.pk),
            "The allocation request",
            FiledTicket.Kind.ALLOCATION,
            {"allocation": allocation},
            group=of,
            of=of,
        )
    ]
    for change in allocation.allocationchangerequest_set.select_related("status").order_by("-created", "-pk"):
        targets.append(
            LinkTarget(
                change_correlation_id(change.pk),
                change_label(change),
                FiledTicket.Kind.ALLOCATION_CHANGE,
                {"allocation_change": change, "allocation": allocation},
                group=of,
                of=of,
            )
        )
    return targets


def link_ticket(number, target: LinkTarget, linked_by) -> Linked:
    """Adopt incident `number` as the ticket for `target`. Raises LinkError if it can't be."""
    number = number.strip().upper()
    if not INCIDENT_NUMBER.match(number):
        raise LinkError(f"{number!r} isn't an incident number (INC followed by digits)")
    client = client_from_settings()

    incident = client.find_incident_by_number(number)
    if incident is None:
        raise LinkError(f"There's no incident {number} in ServiceNow")
    if incident.correlation_id and incident.correlation_id != target.correlation_id:
        raise LinkError(f"{number} is already linked to something else ({incident.correlation_id})")
    if incident.correlation_id != target.correlation_id and incident.state in DONE_STATES:
        raise LinkError(f"{number} is already resolved, so ColdFront would have nothing to send to it")

    ours = client.find_incident(target.correlation_id)
    duplicate = None
    if ours and ours.sys_id != incident.sys_id:
        if ours.state in DONE_STATES:
            raise LinkError(
                f"ColdFront's {ours.number} for this is already resolved. Close {number} in ServiceNow "
                f"by hand, as a duplicate of {ours.number}."
            )
        # Clear its correlation_id first: if setting ours then fails, nothing shares one, and
        # linking again just sets it. Closed, like resolved, means assigned (see _resolve_or_file).
        client.update_incident(
            ours.sys_id,
            {
                **default_assignment(ours),
                "correlation_id": "",
                "u_duplicate_of": incident.sys_id,
                "state": STATE_CLOSED,
                "close_notes": DUPLICATE_CLOSE_NOTES,
            },
        )
        duplicate = ours
        logger.info("Closed %s as a duplicate of %s (%s)", ours.number, number, target.correlation_id)

    if incident.correlation_id != target.correlation_id:
        client.update_incident(incident.sys_id, {"correlation_id": target.correlation_id})
    FiledTicket.objects.update_or_create(
        correlation_id=target.correlation_id,
        defaults={
            "kind": target.kind,
            "number": incident.number,
            "sys_id": incident.sys_id,
            "source": FiledTicket.Source.LINKED,
            "linked_by": linked_by,
            "linked_at": timezone.now(),
            "resolved_at": None,
            **target.subjects,
        },
    )
    logger.info("%s linked %s to %s", linked_by.username, number, target.correlation_id)
    return Linked(incident=incident, duplicate=duplicate)


def unlink_ticket(filed: FiledTicket, unlinked_by) -> None:
    """Undo a link, before the ticket is resolved. Raises LinkError if it can't be undone.

    Clears the incident's correlation_id, with an internal work note saying who unlinked it, and
    deletes the FiledTicket, so ColdFront no longer treats the incident as the request's: the request
    can be linked again, and with no link its resolve files a ticket already resolved. If linking
    closed ColdFront's own incident as a duplicate, that one stays closed.
    """
    if filed.source != FiledTicket.Source.LINKED:
        raise LinkError(f"ColdFront filed {filed.number} itself; only linked tickets can be unlinked")
    if filed.resolved_at:
        raise LinkError(f"ColdFront has resolved {filed.number}, so it can't be unlinked")
    client = client_from_settings()
    incident = client.find_incident_by_number(filed.number)
    if incident is not None:
        if incident.state in DONE_STATES:
            raise LinkError(f"{filed.number} is already resolved in ServiceNow, so it can't be unlinked")
        # Leave the correlation_id alone if someone has since pointed it elsewhere.
        if incident.correlation_id == filed.correlation_id:
            client.update_incident(
                incident.sys_id,
                {
                    "correlation_id": "",
                    "work_notes": f"Unlinked from ColdFront ({filed.correlation_id}) by {unlinked_by.username}.",
                },
            )
    filed.delete()
    logger.info("%s unlinked %s from %s", unlinked_by.username, filed.number, filed.correlation_id)
