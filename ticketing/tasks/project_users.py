"""Already-resolved notices about a project's users: one generic notice per project per day when its
users change, for the PI and Approvers, and one to each user added, telling them about it."""

import logging

from django.contrib.auth import get_user_model

from coldfront.core.project.models import Project
from coldfront.core.utils.mail import build_link

from ticketing.allocation_updates import (
    AllocationUpdate,
)
from ticketing.client import IncidentRef
from ticketing.models import FiledTicket
from ticketing.slots import (
    TicketSlot,
    project_user_added_slot,
    project_users_slot,
)
from ticketing.tasks.filing import (
    resolve_or_file,
)
from ticketing.tasks.people import (
    is_owner_or_approver,
    people_fields,
)
from ticketing.text import render_ticket_text
from ticketing.tickets import (
    U_ACTION_MOVE_ADD_CHANGE,
    clean_short_description,
)

logger = logging.getLogger(__name__)


def create_project_users_ticket(project_pk: int, requestor_pk: int | None = None) -> IncidentRef:
    """Tell a project's PI and Approvers that its users have changed today, and close out the ticket.

    The message is generic -- it names no users and doesn't say whether they were added or
    removed -- so one ticket covers every change to the project's users that day, found by the
    correlation_id "coldfront:project-users:<project pk>:<YYYY-MM-DD>" (in TIME_ZONE). Usually there
    is none yet and one is filed already resolved; later changes that day find it resolved and send
    nothing. A ticket filed in ServiceNow and linked for the day (ticketing.linking) is still open,
    and is resolved with the message instead. People as for allocation requests (people_fields):
    the PI is caller, whoever made the change is contact, the PI standing in without `requestor_pk`, and
    the PI and Approvers are watch-listed.
    """
    project = Project.objects.select_related("pi").get(pk=project_pk)
    slot = project_users_slot(project)

    filed = filed_today(slot)
    if filed:
        return filed
    url = build_link(project.get_absolute_url())
    return resolve_or_file(
        slot,
        people=lambda: people_fields(project, requestor_pk),
        describe=lambda: AllocationUpdate(
            short_description=clean_short_description(f"Project Users Changed | {project.title}"),
            comments=render_ticket_text(
                "ticketing/project_users_changed_notification.txt", {"project": project, "url": url}
            ),
            close_notes="Project users updated.",
            business_service="Research Data Storage",
            u_action=U_ACTION_MOVE_ADD_CHANGE,
        ),
    )


def create_project_user_added_ticket(project_pk: int, user_pk: int, requestor_pk: int | None = None) -> IncidentRef:
    """Tell a user they've been added to a project, with how to reach its storage, and close out the ticket.

    The user is the contact person, since ServiceNow emails a customer-visible comment to the contact
    person (not the caller); the PI is the caller, the Affected Client as on every project ticket.
    Whoever added them decides contact_type, the PI standing in without `requestor_pk`, but isn't on
    the ticket. Nobody is watch-listed: the PI and Approvers hear from create_project_users_ticket,
    and this message is for the new member. One per user, project and day, found by the
    correlation_id "coldfront:project-user-added:<project pk>:<utln>:<YYYY-MM-DD>" (in TIME_ZONE), so
    a re-run, or a re-add the same day, sends nothing more.
    """
    project = Project.objects.select_related("pi").get(pk=project_pk)
    user = get_user_model().objects.get(pk=user_pk)
    slot = project_user_added_slot(project, user)

    filed = filed_today(slot)
    if filed:
        return filed
    return resolve_or_file(
        slot,
        people=lambda: added_user_people(project, user, requestor_pk),
        describe=lambda: AllocationUpdate(
            short_description=clean_short_description(f"Added to Project | {project.title}"),
            comments=render_ticket_text(
                "ticketing/project_user_added_notification.txt",
                {"project": project, "added_by": named_adder(project, requestor_pk)},
            ),
            close_notes="User added to project.",
            business_service="Research Data Storage",
            u_action=U_ACTION_MOVE_ADD_CHANGE,
        ),
    )


def named_adder(project, requestor_pk):
    """Who the added user's message says added them: the requestor if they're the project's owner (PI)
    or one of its Approvers, people the new member knows. None for staff acting for the project, and
    when the requestor isn't known: the PI stands in elsewhere, but isn't claimed to have done it."""
    requestor = get_user_model().objects.filter(pk=requestor_pk).first() if requestor_pk else None
    return requestor if requestor and is_owner_or_approver(requestor, project) else None


def added_user_people(project, user, requestor_pk):
    """people_fields with the added user as contact person, and no watch list."""
    requestor, fields = people_fields(project, requestor_pk)
    return requestor, {**fields, "u_contact_person": user.username, "watch_list": None}


def filed_today(slot: TicketSlot) -> IncidentRef | None:
    """The incident already filed and resolved for `slot`, from the local record, if there is one.

    These notices are filed once and never reopened, so a resolved record means there's nothing to
    send, without asking ServiceNow.
    """
    filed = FiledTicket.objects.filter(correlation_id=slot.correlation_id, resolved_at__isnull=False).first()
    if filed is None:
        return None
    logger.info("Incident for %s today already filed as %s", slot.what, filed.number)
    return IncidentRef(sys_id=filed.sys_id, number=filed.number)
