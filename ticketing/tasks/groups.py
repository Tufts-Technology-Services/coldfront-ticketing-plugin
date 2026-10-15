"""Group membership tickets: a user added to or removed from a project group.

Unused: the trigger is to be a batch (see "Group membership tickets" in docs/plan.md). These record
nothing locally.
"""

import logging

from coldfront.core.utils.common import import_from_settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils import timezone

from ticketing import client as servicenow
from ticketing.client import IncidentRef
from ticketing.permissions import is_ticketing_admin
from ticketing.tickets import (
    CLOSE_CODE_SOLVED_REMOTELY,
    CONTACT_TYPE_EMAIL,
    CONTACT_TYPE_SELF_SERVICE,
    STATE_RESOLVED,
    U_ACTION_MOVE_ADD_CHANGE,
    ServiceNowIncident,
)

logger = logging.getLogger(__name__)


def create_group_membership_ticket(
    username: str, group: str, path: str, user_path: str, requestor_username: str | None = None
) -> IncidentRef | None:
    """File an already-resolved incident telling a user they've been added to a project group.

    Mirrors rt-ticket-utils' hpc_added_to_group_ticket_text: the notification covers the group
    directory, the user's own subdirectory and how to check access. See _group_ticket for who's
    who, admins and re-runs.
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
) -> IncidentRef | None:
    """File an already-resolved incident telling a user they've been removed from a project group.

    No rt-ticket-utils counterpart; the notification says which group and directory they've lost
    access to. See _group_ticket for who's who, admins and re-runs.
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
) -> IncidentRef | None:
    """File an already-resolved incident about `username` joining or leaving `group`.

    Ticketing admins get no ticket (returns None): they're staff, not customers of the group.

    The notification goes in the customer-visible `comments`, so ServiceNow emails the user, who is
    the caller. `requestor_username` is whoever asked for the change -- typically the PI or a
    manager; they become the contact person and are watch-listed. Without it the user stands in. A
    ticketing-admin requestor (ticketing.permissions) is taken to be an administrator acting on someone's behalf
    (contact_type email).

    Group and paths are passed in by whatever made the change, so this plugin needn't know the
    cluster layout. Safe to re-run the same day: an incident already filed today for this change,
    user and group (found by correlation_id "coldfront:group-<change>:<group>:<utln>:<YYYY-MM-DD>",
    in TIME_ZONE) is returned instead of filing another. On a later day -- say the user is re-added
    after being removed -- a new one is filed.
    """
    User = get_user_model()
    user = User.objects.get(username=username)
    what = f"group {change} of {user.username} in {group}"
    if is_ticketing_admin(user):
        logger.info("No incident for %s: ticketing admin", what)
        return None
    requestor = User.objects.get(username=requestor_username) if requestor_username else user
    correlation_id = f"coldfront:group-{change}:{group}:{user.username}:{timezone.localdate().isoformat()}"
    client = servicenow.client_from_settings()

    existing = client.find_incident(correlation_id)
    if existing:
        logger.info("Incident for %s already filed as %s", what, existing.number)
        return existing

    on_behalf = requestor.pk != user.pk and is_ticketing_admin(requestor)
    payload = ServiceNowIncident(
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
    incident = client.create_incident(payload)
    logger.info("Filed %s for %s", incident.number, what)
    return incident
