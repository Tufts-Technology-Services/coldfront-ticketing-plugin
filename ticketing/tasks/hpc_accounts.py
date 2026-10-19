"""HPC account tickets: opened when the user has no UID yet, resolved when the account is created,
and an admin-only child when setting the account up fails.

create_hpc_account_ticket is queued by the hpc_account_requested receiver in ticketing/signals.py,
resolve_hpc_account_ticket (and resolve_hpc_account_failure_ticket, after a failure) by the
hpc_account_provisioned one, and file_hpc_account_failure_ticket by hpc_account_provisioning_failed.
"""

import logging

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from ticketing import client as servicenow
from ticketing.allocation_updates import (
    AllocationUpdate,
    RequestDescription,
)
from ticketing.client import IncidentRef
from ticketing.slots import (
    hpc_account_correlation_id,
    hpc_account_failure_slot,
    hpc_account_slot,
)
from ticketing.tasks.filing import (
    default_assignment,
    known_user,
    open_ticket,
    record_filed,
    recorded_ref,
    resolution_fields,
    resolve_or_file,
)
from ticketing.tickets import (
    CONTACT_TYPE_MONITORING,
    CONTACT_TYPE_SELF_SERVICE,
    DEFAULT_BUSINESS_SERVICE,
    DONE_STATES,
    STATE_ACTIVE,
    STATE_CLOSED,
    STATE_RESOLVED,
    U_ACTION_ACTIVATE,
    ServiceNowIncident,
    clean_short_description,
)

logger = logging.getLogger(__name__)


def _hpc_account_people(user):
    """The user signed up for the account themselves, so they are contact person and caller."""
    return user, {
        "u_contact_person": user.username,
        "caller_id": user.username,
        "contact_type": CONTACT_TYPE_SELF_SERVICE,
    }


def create_hpc_account_ticket(username: str) -> IncidentRef:
    """Open an incident for a requested HPC account that can't be set up straight away.

    Tells the user, in a customer-visible comment that ServiceNow emails them, that their request has
    been received, that there will be a wait, and that they'll hear again once the account has been
    provisioned. The description holds the same generic text. Opened unassigned with no state, so it
    waits New in the group queue. resolve_hpc_account_ticket finds it by the shared correlation_id
    "coldfront:hpc-account:<utln>" once the account exists. Safe to re-run.
    """
    user = get_user_model().objects.get(username=username)
    message = render_to_string("ticketing/hpc_account_request.txt", {"user": user})
    return open_ticket(
        hpc_account_slot(user),
        people=lambda: _hpc_account_people(user),
        describe=lambda requestor: RequestDescription(
            short_description="HPC Account Request",
            description=message,
            comments=message,
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


def file_hpc_account_failure_ticket(username: str, reason: str = "") -> IncidentRef:
    """Tell admins that setting up an HPC account failed, in a ticket only admins are on.

    `reason` is the message hpc_account_provisioning_failed was sent with. The ticket is a child
    (parent) of the account's own incident when there is one -- create_hpc_account_ticket's, for an
    account that had to wait for a UID -- and has no parent otherwise. A signup has no approving
    admin, so the service account (TICKETING_ASSIGNED_TO) is caller and contact, with no watch list,
    contact_type monitoring, and no assignee, so it waits in the group queue. Its text is internal
    (description, work_notes), so the user isn't emailed.

    One per account, by correlation_id "coldfront:hpc-account:<utln>:failed". A repeat failure adds a
    work note, reopening the ticket if it was resolved; a Closed one is left alone. A later success
    resolves it (resolve_hpc_account_failure_ticket). Safe to re-run, though a re-run of a repeat adds
    its work note again.
    """
    user = get_user_model().objects.get(username=username)
    failure = hpc_account_failure_slot(user)
    client = servicenow.client_from_settings()

    existing = client.find_incident(failure.correlation_id)
    if existing:
        if existing.state == STATE_CLOSED:
            logger.warning("%s failed again (%s), but its ticket %s is closed", failure.what, reason, existing.number)
            return existing
        reopen = existing.state == STATE_RESOLVED
        note = {"work_notes": f"Setting up the HPC account failed again: {reason or '(no reason given)'}"}
        client.update_incident(existing.sys_id, {**note, "state": STATE_ACTIVE} if reopen else note)
        logger.info("Noted a repeat failure of %s on %s", failure.what, existing.number)
        record_filed(existing, failure, resolved=False if reopen else None)
        return existing

    account = hpc_account_correlation_id(username)
    parent = recorded_ref(account) or client.find_incident(account)
    person = known_user(client, None, failure.what)
    payload = ServiceNowIncident(
        u_contact_person=person,
        caller_id=person,
        contact_type=CONTACT_TYPE_MONITORING,
        short_description=clean_short_description(f"HPC Account Setup Failed | {username}"),
        description=render_to_string(
            "ticketing/hpc_account_failure.txt", {"user": user, "reason": reason, "parent": parent}
        ),
        u_business_service=DEFAULT_BUSINESS_SERVICE,
        u_action=U_ACTION_ACTIVATE,
        parent=parent.number if parent else None,
        correlation_id=failure.correlation_id,
    )
    incident = client.create_incident(payload)
    logger.info("Filed %s for %s", incident.number, failure.what)
    record_filed(incident, failure)
    return incident


def resolve_hpc_account_failure_ticket(username: str) -> IncidentRef | None:
    """Resolve an HPC account's failure ticket once setting it up has succeeded; no-op if there's none open.

    Queued alongside the account's welcome, only when a failure ticket is recorded and not yet
    resolved (see ticketing.signals). Assigned to TICKETING_ASSIGNED_TO if no one picked it up.
    """
    user = get_user_model().objects.get(username=username)
    failure = hpc_account_failure_slot(user)
    client = servicenow.client_from_settings()
    existing = client.find_incident(failure.correlation_id)
    if existing is None or existing.state in DONE_STATES:
        return existing
    client.update_incident(
        existing.sys_id,
        {
            **default_assignment(existing),
            **resolution_fields("Setting up the HPC account succeeded on a later attempt."),
        },
    )
    logger.info("Resolved %s for %s", existing.number, failure.what)
    record_filed(existing, failure, resolved=True)
    return existing
