"""Filing, resolving and recording the incident that fills a ticket slot: what every task is built on.

open_ticket opens an admin-facing incident for a request, or returns the one already filed; it
leaves state and assigned_to unset so the incident waits New in the assignment group's queue.
resolve_or_file posts a customer-facing message as a comment and resolves, assigning the incident
to TICKETING_ASSIGNED_TO if no one has picked it up; with no incident to resolve it files one
already resolved. Each ticket filed, found or resolved is recorded as a FiledTicket (record_filed),
for links from ColdFront's pages. File-only paths check that record before asking ServiceNow;
resolving always asks ServiceNow, which has the incident's current state.

The ServiceNow client is reached through ticketing.client (servicenow.client_from_settings()), so
tests replace it in one place.
"""

import logging

from coldfront.core.utils.common import import_from_settings
from django.utils import timezone

from ticketing import client as servicenow
from ticketing.allocation_updates import (
    UnsupportedUpdate,
)
from ticketing.client import IncidentRef, UnknownReference
from ticketing.models import FiledTicket
from ticketing.slots import (
    TicketSlot,
)
from ticketing.tickets import (
    CLOSE_CODE_SOLVED_REMOTELY,
    DONE_STATES,
    REFERENCE_FIELDS,
    STATE_RESOLVED,
    ServiceNowIncident,
)

logger = logging.getLogger(__name__)


def known_user(client, utln, what) -> str:
    """`utln` if ServiceNow has that user, else TICKETING_ASSIGNED_TO (logged if `utln` was given).
    The lookup is cached, so building the payload doesn't repeat it."""
    service_account = import_from_settings("TICKETING_ASSIGNED_TO")
    if not utln:
        return service_account
    try:
        client.sys_id_for(*REFERENCE_FIELDS["assigned_to"], utln)
    except UnknownReference:
        logger.warning("ServiceNow has no user %s; using %s for %s", utln, service_account, what)
        return service_account
    return utln


def recorded_ref(correlation_id) -> IncidentRef | None:
    """The incident recorded locally for `correlation_id`, if any, sparing a ServiceNow lookup."""
    filed = FiledTicket.objects.filter(correlation_id=correlation_id).first()
    return IncidentRef(sys_id=filed.sys_id, number=filed.number) if filed else None


def default_assignment(incident) -> dict:
    """The update that gives a resolved `incident` an assignee: TICKETING_ASSIGNED_TO, unless someone
    has picked it up already (then nothing)."""
    return {} if incident.assigned_to else {"assigned_to": import_from_settings("TICKETING_ASSIGNED_TO")}


def resolution_fields(close_notes, comments=None) -> dict:
    """The fields that resolve an incident; `comments` is the customer message, if any."""
    fields = {"state": STATE_RESOLVED, "close_code": CLOSE_CODE_SOLVED_REMOTELY, "close_notes": close_notes}
    return {**fields, "comments": comments} if comments else fields


def record_filed(incident, slot: TicketSlot, *, resolved=None):
    """Record `incident` as filling `slot` (its FiledTicket), or update the record. `resolved`
    True stamps resolved_at, False clears it (reopened), and None leaves it alone.

    The ticket is already in ServiceNow, so a failure here is logged rather than raised. A later
    task for the same correlation_id finds it in ServiceNow and records it then.
    """
    defaults = {
        "kind": slot.kind,
        "table": slot.table,
        "number": incident.number,
        "sys_id": incident.sys_id,
        **slot.subjects,
    }
    if resolved is not None:
        defaults["resolved_at"] = timezone.now() if resolved else None
    try:
        FiledTicket.objects.update_or_create(correlation_id=slot.correlation_id, defaults=defaults)
    except Exception:
        logger.exception("Filed %s for %s, but couldn't record it locally", incident.number, slot.correlation_id)


def open_ticket(slot: TicketSlot, *, people, describe) -> IncidentRef:
    """Open an incident for a request, or return the one already filed for it.

    `people()` returns (requestor, the ServiceNowIncident people fields), and `describe(requestor)`
    the RequestDescription; both are only called if a ticket is needed. The incident is recorded as
    filling `slot`.
    """
    existing = recorded_ref(slot.correlation_id)
    if existing:
        logger.info("Incident for %s already filed as %s", slot.what, existing.number)
        return existing
    client = servicenow.client_from_settings()
    existing = client.find_incident(slot.correlation_id)
    if existing:
        logger.info("Incident for %s already filed as %s", slot.what, existing.number)
        record_filed(existing, slot)
        return existing

    requestor, people_fields = people()
    request = describe(requestor)
    payload = ServiceNowIncident(
        **people_fields,
        short_description=request.short_description,
        description=request.description,
        comments=request.comments,
        u_business_service=request.business_service,
        u_action=request.u_action,
        # No assigned_to: the request waits in the assignment group's queue for an admin.
        correlation_id=slot.correlation_id,
    )
    incident = client.create_incident(payload)
    logger.info("Filed %s for %s", incident.number, slot.what)
    record_filed(incident, slot)
    return incident


def resolve_or_file(slot: TicketSlot, *, people, describe, assignee=None) -> IncidentRef | None:
    """Resolve the request's incident with a customer message, or file one already resolved.

    `describe()` returns the AllocationUpdate, or raises UnsupportedUpdate -- then nothing is sent,
    so an open incident stays open for an admin. An incident that is already Resolved or Closed is
    left alone: `comments` is a journal, so re-posting would duplicate the message.

    A resolved incident always has an assignee. `assignee()`, if given, returns a UTLN to assign it
    to over any assignee it has (admin_request_approver), or None. Otherwise requests are opened
    unassigned, so one still unassigned is assigned to TICKETING_ASSIGNED_TO as it is resolved, and
    one an admin has picked up keeps its assignee. A UTLN ServiceNow doesn't know is logged, and
    TICKETING_ASSIGNED_TO stands in.

    Unlike open_ticket this always asks ServiceNow, for the incident's current state; the
    incident is recorded as a FiledTicket as for open_ticket.
    """
    try:
        update = describe()
    except UnsupportedUpdate as e:
        logger.info("No customer message for %s: %s", slot.what, e)
        return None

    resolution = resolution_fields(update.close_notes, update.comments)
    client = servicenow.client_from_settings()
    existing = client.find_incident(slot.correlation_id)
    if existing and existing.state in DONE_STATES:
        logger.info("Incident %s for %s is already resolved", existing.number, slot.what)
        record_filed(existing, slot)
        return existing

    chosen = assignee() if assignee else None
    if chosen:
        chosen = known_user(client, chosen, slot.what)

    if existing:
        assignment = {"assigned_to": chosen} if chosen else default_assignment(existing)
        client.update_incident(existing.sys_id, {**assignment, **resolution})
        logger.info("Resolved %s for %s", existing.number, slot.what)
        incident = existing
    else:
        _, people_fields = people()
        payload = ServiceNowIncident(
            **people_fields,
            short_description=update.short_description,
            u_business_service=update.business_service,
            u_action=update.u_action,
            assigned_to=chosen or known_user(client, None, slot.what),
            correlation_id=slot.correlation_id,
            **resolution,
        )
        incident = client.create_incident(payload)
        logger.info("Filed %s for %s", incident.number, slot.what)
    record_filed(incident, slot, resolved=True)
    return incident
