"""One generic, already-resolved notice per project per day when its users change."""

import logging

from coldfront.core.project.models import Project
from coldfront.core.utils.mail import build_link
from django.template.loader import render_to_string

from ticketing.allocation_updates import (
    AllocationUpdate,
)
from ticketing.client import IncidentRef
from ticketing.models import FiledTicket
from ticketing.slots import (
    project_users_slot,
)
from ticketing.tasks.filing import (
    resolve_or_file,
)
from ticketing.tasks.people import (
    people_fields,
)
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
    whoever made the change is caller and contact, the PI standing in without `requestor_pk`, and
    the PI and Approvers are watch-listed.
    """
    project = Project.objects.select_related("pi").get(pk=project_pk)
    slot = project_users_slot(project)

    filed = FiledTicket.objects.filter(correlation_id=slot.correlation_id, resolved_at__isnull=False).first()
    if filed:
        logger.info("Incident for %s today already filed as %s", slot.what, filed.number)
        return IncidentRef(sys_id=filed.sys_id, number=filed.number)
    url = build_link(project.get_absolute_url())
    return resolve_or_file(
        slot,
        people=lambda: people_fields(project, requestor_pk),
        describe=lambda: AllocationUpdate(
            short_description=clean_short_description(f"Project Users Changed | {project.title}"),
            comments=render_to_string(
                "ticketing/project_users_changed_notification.txt", {"project": project, "url": url}
            ),
            close_notes="Project users updated.",
            business_service="Research Data Storage",
            u_action=U_ACTION_MOVE_ADD_CHANGE,
        ),
    )
