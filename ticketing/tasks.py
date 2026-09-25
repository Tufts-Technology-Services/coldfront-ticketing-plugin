"""Task functions that create and resolve ServiceNow incidents. Invoked via django_q async_task.

    task                              for (once triggered)         correlation_id
    create_hpc_account_ticket         an HPC account is ready      coldfront:hpc-account:<utln>
    create_allocation_ticket          allocation_new               coldfront:allocation:<pk>
    resolve_allocation_ticket         allocation_activate          coldfront:allocation:<pk>
    create_allocation_change_ticket   allocation_change_created    coldfront:allocation-change:<pk>
    resolve_allocation_change_ticket  allocation_change_approved   coldfront:allocation-change:<pk>

Each request lifecycle shares one correlation_id, which is how the resolve task finds the incident
the create task opened, and how any task that is re-run finds its own earlier work instead of filing
a duplicate. Nothing is stored locally. Nothing calls these yet: the triggers are deliberately
deferred (see docs/plan.md).

Create tasks (_open_request) file for every request, supported or not, with an admin-facing
description, and leave state unset so the instance opens the incident Active. Resolve tasks
(_resolve_or_file) post a customer-facing message chosen by resource (ticketing.allocation_updates)
as a comment and resolve; unsupported allocations are left open for an admin, and with no incident
to resolve they file one already resolved.
"""

import logging

from coldfront.core.allocation.models import Allocation, AllocationChangeRequest
from coldfront.core.utils.common import import_from_settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from ticketing.allocation_updates import (
    UnsupportedUpdate,
    describe_activation,
    describe_change,
    describe_new_allocation,
    describe_request,
)
from ticketing.client import Incident, client_from_settings
from ticketing.tickets import Ticket

logger = logging.getLogger(__name__)

# Choice values, checked against the instance's field metadata (/api/now/ui/meta/incident). An
# invalid value is dropped without an error, so don't change these without re-checking.
STATE_RESOLVED = "6"
STATE_CLOSED = "7"
CLOSE_CODE_SOLVED_REMOTELY = "Solved Remotely (Permanently)"
CONTACT_TYPE_SELF_SERVICE = "self-service"
CONTACT_TYPE_EMAIL = "email"
U_ACTION_MOVE_ADD_CHANGE = "Move/Add/Change"
U_ACTION_ACTIVATE = "Activate/Setup/Install"


def create_hpc_account_ticket(username: str, home_path: str) -> Incident:
    """File an already-resolved incident telling a user their HPC account has been created.

    Mirrors the "HPC Account Creation" incidents staff file today: the welcome text goes in
    `comments` (the customer-visible journal ServiceNow notifies the caller from), a short staff
    note in `close_notes`, and the incident is created Resolved so the instance auto-closes it.

    `home_path` is the user's cluster home directory, passed in by whatever provisioned the account
    so this plugin needn't know the cluster layout. Safe to re-run: an incident already filed for
    this user (found by correlation_id) is returned instead of filing another.
    """
    user = get_user_model().objects.get(username=username)
    correlation_id = f"coldfront:hpc-account:{user.username}"
    client = client_from_settings()

    existing = client.find_incident(correlation_id)
    if existing:
        logger.info("HPC account incident for %s already filed as %s", user.username, existing.number)
        return existing

    ticket = Ticket(
        u_contact_person=user.username,
        caller_id=user.username,
        # The user signed up for the account themselves.
        contact_type=CONTACT_TYPE_SELF_SERVICE,
        short_description=f"HPC Account Creation | {home_path}",
        assigned_to=import_from_settings("TICKETING_ASSIGNED_TO"),
        comments=render_to_string("ticketing/hpc_account_creation_notification.txt", {"user": user}),
        correlation_id=correlation_id,
        state=STATE_RESOLVED,
        close_code=CLOSE_CODE_SOLVED_REMOTELY,
        close_notes="Cluster account created!",
    )
    incident = client.create_incident(ticket)
    logger.info("Filed %s for HPC account %s", incident.number, user.username)
    return incident


def contact_type_for(user, project) -> str:
    """self-service if `user` filed for their own project (as PI or Manager), else email.

    Only superusers and a project's Managers pass the request views' permission checks, so "else"
    means an administrator acting on someone's behalf. Membership is tested directly because
    project.has_perm() is True for every superuser. See "Choosing contact_type" in docs/plan.md.
    """
    if project.pi_id == user.pk:
        return CONTACT_TYPE_SELF_SERVICE
    is_manager = project.projectuser_set.filter(
        user=user, role__name="Manager", status__name__in=("Active", "New")
    ).exists()
    return CONTACT_TYPE_SELF_SERVICE if is_manager else CONTACT_TYPE_EMAIL


def _people_fields(project, requestor_pk):
    """The requestor, and the Ticket people fields for a request about `project`.

    Without `requestor_pk` (the signals don't carry it until the companion core change lands), the
    PI stands in. The PI is watch-listed when someone else asked.
    """
    requestor = get_user_model().objects.get(pk=requestor_pk) if requestor_pk else project.pi
    fields = {
        "u_contact_person": requestor.username,
        "caller_id": requestor.username,
        "watch_list": [project.pi.username] if project.pi_id != requestor.pk else None,
        "contact_type": contact_type_for(requestor, project),
    }
    return requestor, fields


def _open_request(*, correlation_id, what, project, requestor_pk, describe, u_action) -> Incident:
    """Open an Active incident for a request, or return the one already filed for it.

    `describe(requestor)` returns the RequestDescription; it is only called if a ticket is needed.
    """
    client = client_from_settings()
    existing = client.find_incident(correlation_id)
    if existing:
        logger.info("Incident for %s already filed as %s", what, existing.number)
        return existing

    requestor, people = _people_fields(project, requestor_pk)
    request = describe(requestor)
    business_service = {"u_business_service": request.business_service} if request.business_service else {}
    ticket = Ticket(
        **people,
        short_description=request.short_description,
        description=request.description,
        u_action=u_action,
        assigned_to=import_from_settings("TICKETING_ASSIGNED_TO"),
        correlation_id=correlation_id,
        **business_service,
    )
    incident = client.create_incident(ticket)
    logger.info("Filed %s for %s", incident.number, what)
    return incident


def _resolve_or_file(*, correlation_id, what, project, requestor_pk, describe, u_action) -> Incident | None:
    """Resolve the request's incident with a customer message, or file one already resolved.

    `describe()` returns the AllocationUpdate, or raises UnsupportedUpdate -- then nothing is sent,
    so an open incident stays open for an admin. An incident that is already Resolved or Closed is
    left alone: `comments` is a journal, so re-posting would duplicate the message.
    """
    try:
        update = describe()
    except UnsupportedUpdate as e:
        logger.info("No customer message for %s: %s", what, e)
        return None

    resolution = {
        "state": STATE_RESOLVED,
        "close_code": CLOSE_CODE_SOLVED_REMOTELY,
        "close_notes": update.close_notes,
        "comments": update.comments,
    }
    client = client_from_settings()
    existing = client.find_incident(correlation_id)
    if existing:
        if existing.state in (STATE_RESOLVED, STATE_CLOSED):
            logger.info("Incident %s for %s is already resolved", existing.number, what)
        else:
            client.update_incident(existing.sys_id, resolution)
            logger.info("Resolved %s for %s", existing.number, what)
        return existing

    _, people = _people_fields(project, requestor_pk)
    ticket = Ticket(
        **people,
        short_description=update.short_description,
        u_business_service=update.business_service,
        u_action=u_action,
        assigned_to=import_from_settings("TICKETING_ASSIGNED_TO"),
        correlation_id=correlation_id,
        **resolution,
    )
    incident = client.create_incident(ticket)
    logger.info("Filed %s for %s", incident.number, what)
    return incident


# -- New allocations: opened on allocation_new, resolved on allocation_activate ------------------


def _allocation(allocation_pk):
    return Allocation.objects.select_related("project__pi").get(pk=allocation_pk)


def create_allocation_ticket(allocation_pk: int, requestor_pk: int | None = None) -> Incident:
    """Open an incident for a newly requested allocation.

    Every allocation request gets one, supported or not (see describe_new_allocation): once core's
    ticket emails are gated off it is the only ticket admins get. resolve_allocation_ticket finds it
    by the shared correlation_id "coldfront:allocation:<pk>". Opened with no state, so the instance
    starts it Active. Safe to re-run.
    """
    allocation = _allocation(allocation_pk)
    return _open_request(
        correlation_id=f"coldfront:allocation:{allocation.pk}",
        what=f"allocation {allocation.pk}",
        project=allocation.project,
        requestor_pk=requestor_pk,
        describe=lambda requestor: describe_new_allocation(allocation, requestor),
        u_action=U_ACTION_ACTIVATE,
    )


def resolve_allocation_ticket(allocation_pk: int, requestor_pk: int | None = None) -> Incident | None:
    """Tell the requester their allocation is active, and close out its ticket.

    The message depends on the allocation's resource (ticketing.allocation_updates); only Storage
    is supported today, and anything else is left open for an admin. With no incident to resolve, a
    new one is filed already resolved. `requestor_pk` only matters then -- without it the PI is the
    caller. Safe to re-run.
    """
    allocation = _allocation(allocation_pk)
    return _resolve_or_file(
        correlation_id=f"coldfront:allocation:{allocation.pk}",
        what=f"allocation {allocation.pk}",
        project=allocation.project,
        requestor_pk=requestor_pk,
        describe=lambda: describe_activation(allocation),
        u_action=U_ACTION_ACTIVATE,
    )


# -- Change requests: opened on allocation_change_created, resolved on allocation_change_approved -


def _change(allocation_change_pk):
    return AllocationChangeRequest.objects.select_related("allocation__project__pi").get(pk=allocation_change_pk)


def create_allocation_change_ticket(allocation_change_pk: int, requestor_pk: int | None = None) -> Incident:
    """Open an incident for a newly submitted allocation change request.

    Every change request gets one, supported or not (see describe_request); only supported changes
    are later resolved automatically by resolve_allocation_change_ticket, which finds this incident
    by the shared correlation_id "coldfront:allocation-change:<pk>". Opened with no state, so the
    instance starts it Active. Safe to re-run.
    """
    change = _change(allocation_change_pk)
    return _open_request(
        correlation_id=f"coldfront:allocation-change:{change.pk}",
        what=f"allocation change {change.pk}",
        project=change.allocation.project,
        requestor_pk=requestor_pk,
        describe=lambda requestor: describe_request(change, requestor),
        u_action=U_ACTION_MOVE_ADD_CHANGE,
    )


def resolve_allocation_change_ticket(allocation_change_pk: int, requestor_pk: int | None = None) -> Incident | None:
    """Tell the requester their approved allocation change has been made, and close out its ticket.

    The message depends on the allocation's resource (ticketing.allocation_updates); only Storage
    quota changes are supported today, and anything else is left open for an admin. With no
    incident to resolve, a new one is filed already resolved. `requestor_pk` only matters then --
    without it the PI is the caller. Safe to re-run.
    """
    change = _change(allocation_change_pk)
    return _resolve_or_file(
        correlation_id=f"coldfront:allocation-change:{change.pk}",
        what=f"allocation change {change.pk}",
        project=change.allocation.project,
        requestor_pk=requestor_pk,
        describe=lambda: describe_change(change),
        u_action=U_ACTION_MOVE_ADD_CHANGE,
    )
