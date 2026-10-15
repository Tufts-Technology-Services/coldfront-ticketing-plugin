"""HPC account tickets: opened when the user has no UID yet, resolved when the account is created.

Not triggered yet (see "Not yet wired up" in docs/design.md).
"""

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from ticketing.allocation_updates import (
    AllocationUpdate,
    RequestDescription,
)
from ticketing.client import IncidentRef
from ticketing.slots import (
    hpc_account_slot,
)
from ticketing.tasks.filing import (
    open_ticket,
    resolve_or_file,
)
from ticketing.tickets import (
    CONTACT_TYPE_SELF_SERVICE,
    DEFAULT_BUSINESS_SERVICE,
    U_ACTION_ACTIVATE,
)


def _hpc_account_people(user):
    """The user signed up for the account themselves, so they are contact person and caller."""
    return user, {
        "u_contact_person": user.username,
        "caller_id": user.username,
        "contact_type": CONTACT_TYPE_SELF_SERVICE,
    }


def create_hpc_account_ticket(username: str, home_path: str) -> IncidentRef:
    """Open an incident for an HPC account that can't be created yet because the user has no UID.

    Admin-facing: says who signed up and that the account is waiting on a UID. Opened unassigned
    with no state, so it waits New in the group queue. resolve_hpc_account_ticket finds it by the
    shared correlation_id "coldfront:hpc-account:<utln>" once the account exists. `home_path` is the
    user's cluster home directory, passed in by whatever provisions the account so this plugin
    needn't know the cluster layout. Safe to re-run.
    """
    user = get_user_model().objects.get(username=username)
    return open_ticket(
        hpc_account_slot(user),
        people=lambda: _hpc_account_people(user),
        describe=lambda requestor: RequestDescription(
            short_description=f"HPC Account Creation | {home_path}",
            description=render_to_string("ticketing/hpc_account_request.txt", {"user": user, "home_path": home_path}),
            business_service=DEFAULT_BUSINESS_SERVICE,
            u_action=U_ACTION_ACTIVATE,
        ),
    )


def resolve_hpc_account_ticket(username: str, home_path: str) -> IncidentRef:
    """Welcome the user to their new HPC account, and close out its ticket.

    Mirrors the "HPC Account Creation" incidents staff file today: the welcome text goes in
    `comments` (the customer-visible journal ServiceNow notifies the caller from) and a short staff
    note in `close_notes`. Resolves the incident create_hpc_account_ticket opened, if there is one;
    for the usual account, which had a UID all along, there isn't, so one is filed already
    resolved. Safe to re-run: an incident already resolved is left alone.
    """
    user = get_user_model().objects.get(username=username)
    return resolve_or_file(
        hpc_account_slot(user),
        people=lambda: _hpc_account_people(user),
        describe=lambda: AllocationUpdate(
            short_description=f"HPC Account Creation | {home_path}",
            comments=render_to_string("ticketing/hpc_account_creation_notification.txt", {"user": user}),
            close_notes="Cluster account created!",
            business_service=DEFAULT_BUSINESS_SERVICE,
            u_action=U_ACTION_ACTIVATE,
        ),
    )
