"""Task functions that create and resolve ServiceNow incidents. Invoked via django_q async_task.

Implemented: create_hpc_account_ticket.

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

from coldfront.core.utils.common import import_from_settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from ticketing.client import Incident, client_from_settings
from ticketing.tickets import Ticket

logger = logging.getLogger(__name__)

# Choice values, checked against the instance's field metadata (/api/now/ui/meta/incident). An
# invalid value is dropped without an error, so don't change these without re-checking.
STATE_RESOLVED = "6"
CLOSE_CODE_SOLVED_REMOTELY = "Solved Remotely (Permanently)"
CONTACT_TYPE_SELF_SERVICE = "self-service"


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
