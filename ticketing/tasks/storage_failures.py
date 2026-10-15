"""An admin-only child ticket when a storage job fails, resolved when a later attempt succeeds."""

import logging

from django.template.loader import render_to_string

from ticketing import client as servicenow
from ticketing.allocation_updates import (
    BUSINESS_SERVICE_BY_RESOURCE_TYPE,
    PATH_ATTRIBUTE_NAME,
    QUOTA_ATTRIBUTE_NAME,
    resource_label,
)
from ticketing.client import IncidentRef
from ticketing.slots import (
    AllocationTicketSlot,
)
from ticketing.tasks.filing import (
    default_assignment,
    known_user,
    record_filed,
    recorded_ref,
    resolution_fields,
)
from ticketing.tasks.people import (
    request_approver,
)
from ticketing.tickets import (
    CONTACT_TYPE_MONITORING,
    DONE_STATES,
    STATE_ACTIVE,
    STATE_CLOSED,
    STATE_RESOLVED,
    U_ACTION_ACTIVATE,
    U_ACTION_MOVE_ADD_CHANGE,
    ServiceNowIncident,
    clean_short_description,
)

logger = logging.getLogger(__name__)


def file_storage_failure_ticket(
    allocation_pk: int, allocation_change_pk: int | None = None, state: str = "failed"
) -> IncidentRef | None:
    """Tell admins a storage job failed, in a ticket only admins are on.

    `state` is the storage plugin's failed value (share_creation_state for an allocation,
    quota_update_state for `allocation_change_pk`). The ticket is a child (parent) of the
    request's incident -- ColdFront's own, or the customer's if an admin linked it -- or has no
    parent when there is none, as for a ticketing admin's unlinked request. The approving admin is
    caller and contact (TICKETING_ASSIGNED_TO if unknown), with no watch list, contact_type
    monitoring, and no assignee, so it waits in the group queue. Its text is internal (description,
    work_notes), so no customer is emailed.

    One per request, by correlation_id "<request's correlation_id>:storage-failed". A repeat failure
    adds a work note, reopening the ticket if it was resolved; a Closed one is left alone. A later
    success resolves it (resolve_storage_failure_ticket). Safe to re-run, though a re-run of a
    repeat adds its work note again.
    """
    request = AllocationTicketSlot.load(allocation_pk, allocation_change_pk)
    failure = request.storage_failure()
    client = servicenow.client_from_settings()

    existing = client.find_incident(failure.correlation_id)
    if existing:
        if existing.state == STATE_CLOSED:
            logger.warning("%s failed again (%s), but its ticket %s is closed", failure.what, state, existing.number)
            return existing
        reopen = existing.state == STATE_RESOLVED
        note = {"work_notes": f"{request.job} failed again: {state}"}
        client.update_incident(existing.sys_id, {**note, "state": STATE_ACTIVE} if reopen else note)
        logger.info("Noted a repeat failure of %s on %s", failure.what, existing.number)
        record_filed(existing, failure, resolved=False if reopen else None)
        return existing

    parent = recorded_ref(request.correlation_id) or client.find_incident(request.correlation_id)
    approver = request_approver(request.request, request.approved_status)
    person = known_user(client, approver, failure.what)
    allocation = request.allocation
    resource = allocation.get_parent_resource
    label = resource_label(allocation, resource)
    context = {
        "job": request.job,
        "state": state,
        "allocation": allocation,
        "project": allocation.project,
        "pi": allocation.project.pi,
        "resource": resource,
        "path": allocation.get_attribute(PATH_ATTRIBUTE_NAME) or "",
        "quota": allocation.get_attribute(QUOTA_ATTRIBUTE_NAME),
        "parent": parent,
        "approver": approver,
        "url": request.url,
    }
    payload = ServiceNowIncident(
        u_contact_person=person,
        caller_id=person,
        contact_type=CONTACT_TYPE_MONITORING,
        short_description=clean_short_description(f"{label}: Storage Job Failed | {allocation.project.title}"),
        description=render_to_string("ticketing/storage_failure.txt", context),
        u_business_service=BUSINESS_SERVICE_BY_RESOURCE_TYPE["Storage"],
        u_action=U_ACTION_MOVE_ADD_CHANGE if request.is_change else U_ACTION_ACTIVATE,
        parent=parent.number if parent else None,
        correlation_id=failure.correlation_id,
    )
    incident = client.create_incident(payload)
    logger.info("Filed %s for %s", incident.number, failure.what)
    record_filed(incident, failure)
    return incident


def resolve_storage_failure_ticket(allocation_pk: int, allocation_change_pk: int | None = None) -> IncidentRef | None:
    """Resolve a storage job's failure ticket once the job has succeeded; no-op if there's none open.

    Queued alongside the request's own resolve, only when a failure ticket is recorded and not yet
    resolved (see ticketing.signals). Assigned to TICKETING_ASSIGNED_TO if no one picked it up.
    """
    request = AllocationTicketSlot.load(allocation_pk, allocation_change_pk)
    failure = request.storage_failure()
    client = servicenow.client_from_settings()
    existing = client.find_incident(failure.correlation_id)
    if existing is None or existing.state in DONE_STATES:
        return existing
    client.update_incident(
        existing.sys_id,
        {**default_assignment(existing), **resolution_fields(f"{request.job} succeeded on a later attempt.")},
    )
    logger.info("Resolved %s for %s", existing.number, failure.what)
    record_filed(existing, failure, resolved=True)
    return existing
