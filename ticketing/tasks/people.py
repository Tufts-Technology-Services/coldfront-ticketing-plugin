"""Who is on a ticket about a project, and who approved a request.

The requestor is the contact person; the PI is the caller (the Affected Client, at Tufts); the PI
and Approvers are watch-listed (people_fields). ServiceNow emails a customer-visible comment to the contact person and
the watch list, not the caller (the Tufts "End User" notifications), so anyone who must hear is one
of those. Approvers are found in a request's simple-history (request_approver).
"""

from django.contrib.auth import get_user_model

from ticketing.permissions import is_ticketing_admin
from ticketing.tickets import (
    CONTACT_TYPE_EMAIL,
    CONTACT_TYPE_SELF_SERVICE,
)


def contact_type_for(user, project) -> str:
    """self-service if `user` filed for their own project (as PI or Manager), else email.

    Only superusers and a project's Managers pass the request views' permission checks, so "else"
    means an administrator acting on someone's behalf. Membership is tested directly because
    project.has_perm() is True for every superuser. See "Who is on a ticket" in docs/design.md.
    """
    if project.pi_id == user.pk:
        return CONTACT_TYPE_SELF_SERVICE
    is_manager = project.projectuser_set.filter(
        user=user, role__name="Manager", status__name__in=("Active", "New")
    ).exists()
    return CONTACT_TYPE_SELF_SERVICE if is_manager else CONTACT_TYPE_EMAIL


def project_watchers(project, *people) -> list[str]:
    """UTLNs to watch-list on a ticket about `project`: its PI and Approvers, less `people`.

    `people` are the users already emailed from the ticket (the contact person), who needn't watch
    it too. "Approver" is Tufts' label for ColdFront's Manager role. So a PI's request
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


def people_fields(project, requestor_pk):
    """The requestor, and the ServiceNowIncident people fields for a ticket about `project`.

    The PI is the caller (caller_id): Tufts' Affected Client on anything about an allocation or a
    project. The requestor -- whoever submitted the request -- is the contact person
    (u_contact_person) and decides contact_type. Without `requestor_pk` (the signals don't carry it
    until the companion core change lands), the PI stands in. ServiceNow emails the contact person
    and the watch list but not the caller, so the PI is watch-listed unless they're the contact,
    along with the project's Approvers (project_watchers).
    """
    User = get_user_model()
    requestor = User.objects.get(pk=requestor_pk) if requestor_pk else project.pi
    fields = {
        "u_contact_person": requestor.username,
        "caller_id": project.pi.username,
        "watch_list": project_watchers(project, requestor) or None,
        "contact_type": contact_type_for(requestor, project),
    }
    return requestor, fields


def request_approver(request, approved_status) -> str | None:
    """The UTLN of whoever approved `request`, from its simple-history; None if unknown.

    `request` is an Allocation or AllocationChangeRequest. Its history rows record who made each
    save (HistoryRequestMiddleware); the approver made the latest save where the status became
    `approved_status`. None when that save happened outside a web request.
    """
    approver = previous = None
    for status, username in request.history.order_by("history_date", "history_id").values_list(
        "status__name", "history_user__username"
    ):
        if status == approved_status and previous != approved_status:
            approver = username
        previous = status
    return approver


def admin_request_approver(request, approved_status) -> str | None:
    """The approver's UTLN (request_approver) if a ticketing admin submitted `request`; else None.

    The submitter is whoever made the request's creation save. Admins' requests open no ticket, and
    their resolve is assigned to the approving admin rather than the integration account.
    """
    created = request.history.filter(history_type="+").select_related("history_user").first()
    if created is None or not is_ticketing_admin(created.history_user):
        return None
    return request_approver(request, approved_status)
