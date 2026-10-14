"""Adopting a ticket filed in ServiceNow directly, so ColdFront's resolution lands on it.

Some requests reach ServiceNow first (email, the portal) and an admin then makes the change in
ColdFront. From the ticket card, the admin links the incident to what ColdFront knows the request
as -- an allocation request, a change request, or today's project user changes. Linking gives the
incident that request's correlation_id and records it as a FiledTicket, so the tasks treat it as
their own: the create task finds it and files nothing, and the resolve task resolves it with the
customer message, leaving its caller and watch list alone.

If ColdFront already opened an incident for the request (the admin submitted it on the customer's
behalf, so allocation_new filed one), that one is closed as a duplicate of the linked incident, the
way Tufts staff close duplicates: u_duplicate_of, state Closed, close notes "Duplicate ticket", no
close code. Its correlation_id is cleared first, so two incidents never share one.

Runs in the admin's request rather than a task, so they see at once whether it worked. It is an
explicit act, so the admin ticketing switch doesn't apply.
"""

import logging
import re
from dataclasses import dataclass, field

from coldfront.core.allocation.models import Allocation
from coldfront.core.project.models import Project
from coldfront.core.utils.common import import_from_settings
from django.utils import timezone

from ticketing.client import Incident, client_from_settings
from ticketing.models import FiledTicket
from ticketing.tasks import (
    STATE_CLOSED,
    STATE_RESOLVED,
    allocation_correlation_id,
    change_correlation_id,
    project_users_correlation_id,
)

logger = logging.getLogger(__name__)

INCIDENT_NUMBER = re.compile(r"^INC\d+$")
DUPLICATE_CLOSE_NOTES = "Duplicate ticket"


class LinkError(Exception):
    """Linking was refused; the message is for the admin."""


@dataclass(frozen=True)
class LinkTarget:
    """A request an incident can be linked to."""

    correlation_id: str
    label: str
    kind: str
    subjects: dict = field(compare=False)


@dataclass(frozen=True)
class Linked:
    incident: Incident
    duplicate: Incident | None  # ColdFront's own incident, closed as a duplicate


def link_targets(subject) -> list[LinkTarget]:
    """What an incident can be linked to from `subject`'s page, most likely first.

    An allocation: its request, then its change requests, newest first. A project: today's user
    changes, so the change an admin is about to make resolves the linked incident.
    """
    if isinstance(subject, Allocation):
        targets = [
            LinkTarget(
                allocation_correlation_id(subject.pk),
                "The allocation request",
                FiledTicket.Kind.ALLOCATION,
                {"allocation": subject},
            )
        ]
        for change in subject.allocationchangerequest_set.select_related("status").order_by("-created"):
            targets.append(
                LinkTarget(
                    change_correlation_id(change.pk),
                    f"Change request {change.pk} ({change.status.name}, {change.created:%Y-%m-%d})",
                    FiledTicket.Kind.ALLOCATION_CHANGE,
                    {"allocation_change": change, "allocation": subject},
                )
            )
        return targets
    if isinstance(subject, Project):
        return [
            LinkTarget(
                project_users_correlation_id(subject.pk),
                f"Today's user changes ({timezone.localdate():%Y-%m-%d})",
                FiledTicket.Kind.PROJECT_USERS,
                {"project": subject},
            )
        ]
    raise TypeError(f"Nothing to link a ticket to on a {type(subject).__name__}")


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
    if incident.correlation_id != target.correlation_id and incident.state in (STATE_RESOLVED, STATE_CLOSED):
        raise LinkError(f"{number} is already resolved, so ColdFront would have nothing to send to it")

    ours = client.find_incident(target.correlation_id)
    duplicate = None
    if ours and ours.sys_id != incident.sys_id:
        if ours.state in (STATE_RESOLVED, STATE_CLOSED):
            raise LinkError(
                f"ColdFront's {ours.number} for this is already resolved. Close {number} in ServiceNow "
                f"by hand, as a duplicate of {ours.number}."
            )
        # Clear its correlation_id first: if setting ours then fails, nothing shares one, and
        # linking again just sets it. Closed, like resolved, means assigned (see _resolve_or_file).
        assignment = {} if ours.assigned_to else {"assigned_to": import_from_settings("TICKETING_ASSIGNED_TO")}
        client.update_incident(
            ours.sys_id,
            {
                **assignment,
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
            "resolved_at": None,
            **target.subjects,
        },
    )
    logger.info("%s linked %s to %s", linked_by.username, number, target.correlation_id)
    return Linked(incident=incident, duplicate=duplicate)
