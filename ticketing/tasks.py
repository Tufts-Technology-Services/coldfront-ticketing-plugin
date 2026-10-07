"""Task functions that create and resolve ServiceNow incidents. Invoked via django_q async_task.

    task                              for (once triggered)         correlation_id
    create_hpc_account_ticket         an HPC account is ready      coldfront:hpc-account:<utln>
    create_group_membership_ticket    a user is added to a group   coldfront:group-add:<group>:<utln>:<date>
    create_group_removal_ticket       a user leaves a group        coldfront:group-remove:<group>:<utln>:<date>
    create_allocation_ticket          allocation_new               coldfront:allocation:<pk>
    resolve_allocation_ticket         allocation_activate          coldfront:allocation:<pk>
    create_allocation_change_ticket   allocation_change_created    coldfront:allocation-change:<pk>
    resolve_allocation_change_ticket  allocation_change_approved   coldfront:allocation-change:<pk>

Each request lifecycle shares one correlation_id, which is how the resolve task finds the incident
the create task opened, and how any task that is re-run finds its own earlier work instead of filing
a duplicate. Nothing is stored locally. Nothing calls these yet: the triggers are deliberately
deferred (see docs/plan.md).

Create tasks (_open_request) file for every request, supported or not, with an admin-facing
description, and leave state and assigned_to unset so the incident opens New in the assignment
group's queue. Resolve tasks (_resolve_or_file) post a customer-facing message chosen by resource
(ticketing.allocation_updates) as a comment and resolve, assigning the incident to
TICKETING_ASSIGNED_TO if no one has picked it up; unsupported allocations are left open for an
admin, and with no incident to resolve they file one already resolved.
"""

import logging

from coldfront.core.allocation.models import Allocation, AllocationChangeRequest
from coldfront.core.utils.common import import_from_settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils import timezone

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


def create_group_membership_ticket(
    username: str, group: str, path: str, user_path: str, requestor_username: str | None = None
) -> Incident | None:
    """File an already-resolved incident telling a user they've been added to a project group.

    Mirrors rt-ticket-utils' hpc_added_to_group_ticket_text: the notification covers the group
    directory, the user's own subdirectory and how to check access. See _group_ticket for who's
    who, superusers and re-runs.
    """
    return _group_ticket(
        change="add",
        username=username,
        group=group,
        requestor_username=requestor_username,
        short_description=f"Tier 1 HPC Storage: Grant Access to {user_path}",
        template="ticketing/hpc_added_to_group_notification.txt",
        context={"path": path, "user_path": user_path, "group": group},
        close_notes="Added user to group!",
    )


def create_group_removal_ticket(
    username: str, group: str, path: str, requestor_username: str | None = None
) -> Incident | None:
    """File an already-resolved incident telling a user they've been removed from a project group.

    No rt-ticket-utils counterpart; the notification says which group and directory they've lost
    access to. See _group_ticket for who's who, superusers and re-runs.
    """
    return _group_ticket(
        change="remove",
        username=username,
        group=group,
        requestor_username=requestor_username,
        short_description=f"Tier 1 HPC Storage: Remove Access to {path}",
        template="ticketing/hpc_removed_from_group_notification.txt",
        context={"path": path, "group": group},
        close_notes="Removed user from group!",
    )


def _group_ticket(
    *, change, username, group, requestor_username, short_description, template, context, close_notes
) -> Incident | None:
    """File an already-resolved incident about `username` joining or leaving `group`.

    Superusers get no ticket (returns None): they're staff, not customers of the group.

    The notification goes in the customer-visible `comments`, so ServiceNow emails the user, who is
    the caller. `requestor_username` is whoever asked for the change -- typically the PI or a
    manager; they become the contact person and are watch-listed. Without it the user stands in. A
    superuser requestor is taken to be an administrator acting on someone's behalf (contact_type
    email).

    Group and paths are passed in by whatever made the change, so this plugin needn't know the
    cluster layout. Safe to re-run the same day: an incident already filed today for this change,
    user and group (found by correlation_id "coldfront:group-<change>:<group>:<utln>:<YYYY-MM-DD>",
    in TIME_ZONE) is returned instead of filing another. On a later day -- say the user is re-added
    after being removed -- a new one is filed.
    """
    User = get_user_model()
    user = User.objects.get(username=username)
    what = f"group {change} of {user.username} in {group}"
    if user.is_superuser:
        logger.info("No incident for %s: superuser", what)
        return None
    requestor = User.objects.get(username=requestor_username) if requestor_username else user
    correlation_id = f"coldfront:group-{change}:{group}:{user.username}:{timezone.localdate().isoformat()}"
    client = client_from_settings()

    existing = client.find_incident(correlation_id)
    if existing:
        logger.info("Incident for %s already filed as %s", what, existing.number)
        return existing

    on_behalf = requestor.pk != user.pk and requestor.is_superuser
    ticket = Ticket(
        u_contact_person=requestor.username,
        caller_id=user.username,
        watch_list=[requestor.username] if requestor.pk != user.pk else None,
        contact_type=CONTACT_TYPE_EMAIL if on_behalf else CONTACT_TYPE_SELF_SERVICE,
        short_description=short_description,
        u_business_service="Research Data Storage",
        u_action=U_ACTION_MOVE_ADD_CHANGE,
        assigned_to=import_from_settings("TICKETING_ASSIGNED_TO"),
        comments=render_to_string(template, context),
        correlation_id=correlation_id,
        state=STATE_RESOLVED,
        close_code=CLOSE_CODE_SOLVED_REMOTELY,
        close_notes=close_notes,
    )
    incident = client.create_incident(ticket)
    logger.info("Filed %s for %s", incident.number, what)
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


def project_watchers(project, *people) -> list[str]:
    """UTLNs to watch-list on a ticket about `project`: its PI and Approvers, less `people`.

    `people` are the users already on the ticket (the contact person and affected client), who
    needn't watch it too. "Approver" is Tufts' label for ColdFront's Manager role. So a PI's request
    watch-lists the Approvers, an Approver's request the PI and the other Approvers, and an
    administrator's request all of them. As in core's Project.get_user_emails, the PI is always
    included and Approvers only while Active with notifications enabled for the project.
    """
    approvers = (
        project.projectuser_set.filter(role__name="Manager", status__name="Active", enable_notifications=True)
        .order_by("user__username")
        .values_list("user__username", flat=True)
    )
    on_ticket = {person.username for person in people}
    # The PI is normally a Manager on their own project too; dict.fromkeys drops the repeat.
    return [username for username in dict.fromkeys([project.pi.username, *approvers]) if username not in on_ticket]


def _people_fields(project, requestor_pk, affected_pk=None):
    """The requestor, and the Ticket people fields for a request about `project`.

    The requestor -- whoever submitted the request -- is the contact person (u_contact_person) and
    decides contact_type. The affected client (caller_id) is who the request is for, which ServiceNow
    notifies; without `affected_pk` it is the requestor. Without `requestor_pk` (the signals don't
    carry it until the companion core change lands), the PI stands in. Everyone else in charge of the
    project is watch-listed (project_watchers).
    """
    User = get_user_model()
    requestor = User.objects.get(pk=requestor_pk) if requestor_pk else project.pi
    affected = User.objects.get(pk=affected_pk) if affected_pk else requestor
    fields = {
        "u_contact_person": requestor.username,
        "caller_id": affected.username,
        "watch_list": project_watchers(project, requestor, affected) or None,
        "contact_type": contact_type_for(requestor, project),
    }
    return requestor, fields


def _open_request(*, correlation_id, what, project, requestor_pk, affected_pk, describe, u_action) -> Incident:
    """Open an Active incident for a request, or return the one already filed for it.

    `describe(requestor)` returns the RequestDescription; it is only called if a ticket is needed.
    """
    client = client_from_settings()
    existing = client.find_incident(correlation_id)
    if existing:
        logger.info("Incident for %s already filed as %s", what, existing.number)
        return existing

    requestor, people = _people_fields(project, requestor_pk, affected_pk)
    request = describe(requestor)
    business_service = {"u_business_service": request.business_service} if request.business_service else {}
    ticket = Ticket(
        **people,
        short_description=request.short_description,
        description=request.description,
        u_action=u_action,
        # No assigned_to: the request waits in the assignment group's queue for an admin.
        correlation_id=correlation_id,
        **business_service,
    )
    incident = client.create_incident(ticket)
    logger.info("Filed %s for %s", incident.number, what)
    return incident


def _resolve_or_file(
    *, correlation_id, what, project, requestor_pk, affected_pk, describe, u_action
) -> Incident | None:
    """Resolve the request's incident with a customer message, or file one already resolved.

    `describe()` returns the AllocationUpdate, or raises UnsupportedUpdate -- then nothing is sent,
    so an open incident stays open for an admin. An incident that is already Resolved or Closed is
    left alone: `comments` is a journal, so re-posting would duplicate the message.

    A resolved incident always has an assignee. Requests are opened unassigned, so one still
    unassigned is assigned to TICKETING_ASSIGNED_TO as it is resolved; one an admin has picked up
    keeps its assignee.
    """
    try:
        update = describe()
    except UnsupportedUpdate as e:
        logger.info("No customer message for %s: %s", what, e)
        return None

    assignee = import_from_settings("TICKETING_ASSIGNED_TO")
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
            assignment = {} if existing.assigned_to else {"assigned_to": assignee}
            client.update_incident(existing.sys_id, {**assignment, **resolution})
            logger.info("Resolved %s for %s", existing.number, what)
        return existing

    _, people = _people_fields(project, requestor_pk, affected_pk)
    ticket = Ticket(
        **people,
        short_description=update.short_description,
        u_business_service=update.business_service,
        u_action=u_action,
        assigned_to=assignee,
        correlation_id=correlation_id,
        **resolution,
    )
    incident = client.create_incident(ticket)
    logger.info("Filed %s for %s", incident.number, what)
    return incident


# -- New allocations: opened on allocation_new, resolved on allocation_activate ------------------


def allocation_correlation_id(allocation_pk) -> str:
    return f"coldfront:allocation:{allocation_pk}"


def _allocation(allocation_pk):
    return Allocation.objects.select_related("project__pi").get(pk=allocation_pk)


def create_allocation_ticket(
    allocation_pk: int, requestor_pk: int | None = None, affected_pk: int | None = None
) -> Incident:
    """Open an incident for a newly requested allocation.

    Every allocation request gets one, supported or not (see describe_new_allocation): once core's
    ticket emails are gated off it is the only ticket admins get. resolve_allocation_ticket finds it
    by the shared correlation_id "coldfront:allocation:<pk>". Opened with no state, so the instance
    starts it Active. The requestor is the contact person, and `affected_pk` the affected client if
    it isn't them (see _people_fields). Safe to re-run.
    """
    allocation = _allocation(allocation_pk)
    return _open_request(
        correlation_id=allocation_correlation_id(allocation.pk),
        what=f"allocation {allocation.pk}",
        project=allocation.project,
        requestor_pk=requestor_pk,
        affected_pk=affected_pk,
        describe=lambda requestor: describe_new_allocation(allocation, requestor),
        u_action=U_ACTION_ACTIVATE,
    )


def resolve_allocation_ticket(
    allocation_pk: int, requestor_pk: int | None = None, affected_pk: int | None = None
) -> Incident | None:
    """Tell the requester their allocation is active, and close out its ticket.

    The message depends on the allocation's resource (ticketing.allocation_updates); only Storage
    is supported today, and anything else is left open for an admin. With no incident to resolve, a
    new one is filed already resolved. `requestor_pk` and `affected_pk` only matter then -- without
    them the PI is the caller. Safe to re-run.
    """
    allocation = _allocation(allocation_pk)
    return _resolve_or_file(
        correlation_id=allocation_correlation_id(allocation.pk),
        what=f"allocation {allocation.pk}",
        project=allocation.project,
        requestor_pk=requestor_pk,
        affected_pk=affected_pk,
        describe=lambda: describe_activation(allocation),
        u_action=U_ACTION_ACTIVATE,
    )


# -- Change requests: opened on allocation_change_created, resolved on allocation_change_approved -


def change_correlation_id(allocation_change_pk) -> str:
    return f"coldfront:allocation-change:{allocation_change_pk}"


def _change(allocation_change_pk):
    return AllocationChangeRequest.objects.select_related("allocation__project__pi").get(pk=allocation_change_pk)


def create_allocation_change_ticket(
    allocation_change_pk: int, requestor_pk: int | None = None, affected_pk: int | None = None
) -> Incident:
    """Open an incident for a newly submitted allocation change request.

    Every change request gets one, supported or not (see describe_request); only supported changes
    are later resolved automatically by resolve_allocation_change_ticket, which finds this incident
    by the shared correlation_id "coldfront:allocation-change:<pk>". Opened with no state, so the
    instance starts it Active. The requestor is the contact person, and `affected_pk` the affected
    client if it isn't them (see _people_fields). Safe to re-run.
    """
    change = _change(allocation_change_pk)
    return _open_request(
        correlation_id=change_correlation_id(change.pk),
        what=f"allocation change {change.pk}",
        project=change.allocation.project,
        requestor_pk=requestor_pk,
        affected_pk=affected_pk,
        describe=lambda requestor: describe_request(change, requestor),
        u_action=U_ACTION_MOVE_ADD_CHANGE,
    )


def resolve_allocation_change_ticket(
    allocation_change_pk: int, requestor_pk: int | None = None, affected_pk: int | None = None
) -> Incident | None:
    """Tell the requester their approved allocation change has been made, and close out its ticket.

    The message depends on the allocation's resource (ticketing.allocation_updates); only Storage
    quota changes are supported today, and anything else is left open for an admin. With no
    incident to resolve, a new one is filed already resolved. `requestor_pk` and `affected_pk` only
    matter then -- without them the PI is the caller. Safe to re-run.
    """
    change = _change(allocation_change_pk)
    return _resolve_or_file(
        correlation_id=change_correlation_id(change.pk),
        what=f"allocation change {change.pk}",
        project=change.allocation.project,
        requestor_pk=requestor_pk,
        affected_pk=affected_pk,
        describe=lambda: describe_change(change),
        u_action=U_ACTION_MOVE_ADD_CHANGE,
    )
