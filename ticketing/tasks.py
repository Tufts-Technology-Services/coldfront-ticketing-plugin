"""Task functions that create and resolve ServiceNow incidents. Invoked via django_q async_task.

Implemented: create_hpc_account_ticket, resolve_allocation_change_ticket.

NOT YET IMPLEMENTED -- the allocation tasks, invoked from ticketing.signals. Four of them: create
and resolve an allocation incident; create and resolve a change-request incident. The two resolve
tasks can share one implementation, keyed on which mapping to look up.

Create:

1. Re-query by pk. Skip if the local allocation/change-request -> incident mapping (ticketing.models)
   already has an entry, or if ServiceNow already has an incident with this object's correlation_id
   (e.g. "coldfront:allocation:<pk>"). The second check covers a task that died between the POST and
   saving the mapping, so a retried task never opens a duplicate incident.

2. Build a Ticket (ticketing.tickets) with state/close_code unset so the incident opens. People
   fields are UTLNs, which equal User.username in ColdFront: u_contact_person and caller_id from the
   requestor, watch_list from project.pi, assigned_to from TICKETING_ASSIGNED_TO. Over the Table API
   these are reference fields, so they likely need converting to sys_user sys_ids first (open
   question 3 in docs/plan.md). Truncate short_description; it is single-line. Set contact_type
   explicitly -- left unset, the instance fills in "phone": "self-service" if the requestor is the
   project's PI or an active Manager on it, else "email" (a superuser filing on someone's behalf).
   Check membership directly; project.has_perm() is True for every superuser. See "Choosing
   contact_type" in docs/plan.md.

3. Render the incident description with django.template.loader.render_to_string from
   templates/ticketing/. This is where the re-queried detail lands -- allocation attributes,
   resource, project, quota, dates -- and is the reason the plugin hooks signals at all.
   coldfront_utils (attribute/attribute.py, util/allocations.py) has the attribute helpers; prefer
   them over hand-rolled lookups.

4. Create the incident via ticketing.client and store its sys_id and number in the mapping.

Resolve:

1. Look up the stored incident. If there is none (the object predates the plugin, was created
   outside the request views, or the create task hasn't finished yet), log and return.

2. Update that sys_id with the resolution fields (state, close_code, close_notes) and a
   customer-facing comment rendered from templates/ticketing/. Omit unset fields: sending null on
   an update would blank the field in ServiceNow.
"""

import logging

from coldfront.core.allocation.models import AllocationChangeRequest
from coldfront.core.utils.common import import_from_settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from ticketing.allocation_updates import UnsupportedUpdate, describe_change
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


def resolve_allocation_change_ticket(allocation_change_pk: int, requestor_pk: int | None = None) -> Incident | None:
    """Tell the requester their approved allocation change has been made, and close out its ticket.

    What the message says depends on the allocation's resource (ticketing.allocation_updates); only
    Storage quota changes are supported today. Anything else is logged and left alone, so an open
    incident stays open for an admin to handle.

    If the change request already has an incident (correlation_id
    "coldfront:allocation-change:<pk>"), it is resolved with the message as a customer-visible
    comment -- unless it is already resolved, since re-posting would duplicate the comment in its
    journal. Otherwise a new, already-resolved incident is filed so the user still hears about it.

    `requestor_pk` is who submitted the change; without it, the project's PI stands in as caller.
    """
    change = AllocationChangeRequest.objects.select_related("allocation__project__pi").get(pk=allocation_change_pk)
    try:
        update = describe_change(change)
    except UnsupportedUpdate as e:
        logger.info("No ticket for allocation change %s: %s", change.pk, e)
        return None

    resolution = {
        "state": STATE_RESOLVED,
        "close_code": CLOSE_CODE_SOLVED_REMOTELY,
        "close_notes": update.close_notes,
        "comments": update.comments,
    }
    correlation_id = f"coldfront:allocation-change:{change.pk}"
    client = client_from_settings()

    existing = client.find_incident(correlation_id)
    if existing:
        if existing.state in (STATE_RESOLVED, STATE_CLOSED):
            logger.info("Incident %s for allocation change %s is already resolved", existing.number, change.pk)
        else:
            client.update_incident(existing.sys_id, resolution)
            logger.info("Resolved %s for allocation change %s", existing.number, change.pk)
        return existing

    project = change.allocation.project
    requestor = get_user_model().objects.get(pk=requestor_pk) if requestor_pk else project.pi
    ticket = Ticket(
        u_contact_person=requestor.username,
        caller_id=requestor.username,
        watch_list=[project.pi.username] if project.pi_id != requestor.pk else None,
        contact_type=contact_type_for(requestor, project),
        short_description=update.short_description,
        u_business_service=update.business_service,
        u_action=U_ACTION_MOVE_ADD_CHANGE,
        assigned_to=import_from_settings("TICKETING_ASSIGNED_TO"),
        correlation_id=correlation_id,
        **resolution,
    )
    incident = client.create_incident(ticket)
    logger.info("Filed %s for allocation change %s", incident.number, change.pk)
    return incident
