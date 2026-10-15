"""Ticket slots: the one ticket ColdFront keeps per request or event, whichever incident fills it.

Each kind of ticket has a correlation_id, which is how the tasks find the incident that fills its
slot in ServiceNow (and how a re-run task finds its own earlier work), and a TicketSlot built from
it. AllocationTicketSlot adds the allocation or change request a slot is for. The correlation-id
functions query nothing, so the signal receivers use them inside the user's request.
"""

from dataclasses import dataclass, field

from coldfront.core.allocation.models import Allocation, AllocationChangeRequest
from coldfront.core.utils.mail import build_link
from django.utils import timezone

from ticketing.client import INCIDENT_TABLE
from ticketing.models import FiledTicket


@dataclass(frozen=True, kw_only=True)
class TicketSlot:
    """The one ticket ColdFront keeps for a request or event, whichever incident fills it.

    The tasks fill it with an incident of their own, unless an admin has linked a customer's
    (ticketing.linking). It says which ServiceNow table holds it (table) and how to find it there
    (correlation_id), how to record it (kind and subjects, FiledTicket's fields), and what logs
    call it (what). What the ticket says is decided elsewhere, with the content (the describe_*
    functions); not the payload (tickets.ServiceNowIncident), a reference to an incident
    (client.IncidentRef) or the local record of it (models.FiledTicket).

    Only incidents are filed today, so `table` is always "incident"; see "Requests instead of
    incidents?" in docs/plan.md for what other tables would take.
    """

    correlation_id: str
    what: str
    kind: str
    subjects: dict = field(compare=False)
    table: str = INCIDENT_TABLE


@dataclass(frozen=True, kw_only=True)
class AllocationTicketSlot(TicketSlot):
    """The slot for an allocation or change request's ticket, with the request it's for.

    Built from loaded objects (of_allocation, of_change), or by pk in a task (load). The one place
    that knows each kind's correlation_id, subjects, approval status and storage job.
    """

    allocation: Allocation = field(compare=False)
    request: Allocation | AllocationChangeRequest = field(compare=False)

    @classmethod
    def of_allocation(cls, allocation) -> "AllocationTicketSlot":
        return cls(
            correlation_id=allocation_correlation_id(allocation.pk),
            what=f"allocation {allocation.pk}",
            kind=FiledTicket.Kind.ALLOCATION,
            subjects={"allocation": allocation},
            allocation=allocation,
            request=allocation,
        )

    @classmethod
    def of_change(cls, change) -> "AllocationTicketSlot":
        return cls(
            correlation_id=change_correlation_id(change.pk),
            what=f"allocation change {change.pk}",
            kind=FiledTicket.Kind.ALLOCATION_CHANGE,
            subjects={"allocation_change": change, "allocation": change.allocation},
            allocation=change.allocation,
            request=change,
        )

    @classmethod
    def load(cls, allocation_pk, allocation_change_pk=None) -> "AllocationTicketSlot":
        """The change request's ticket if `allocation_change_pk` is given, else the allocation's."""
        if allocation_change_pk:
            changes = AllocationChangeRequest.objects.select_related("allocation__project__pi")
            return cls.of_change(changes.get(pk=allocation_change_pk))
        return cls.of_allocation(Allocation.objects.select_related("project__pi").get(pk=allocation_pk))

    @property
    def is_change(self) -> bool:
        return isinstance(self.request, AllocationChangeRequest)

    @property
    def approved_status(self) -> str:
        """The request's status once approved (see request_approver)."""
        return "Approved" if self.is_change else "Active"

    @property
    def job(self) -> str:
        """What the storage plugin does once the request is approved."""
        return "Quota change" if self.is_change else "Share creation"

    @property
    def url(self) -> str:
        return build_link(self.request.get_absolute_url())

    def storage_failure(self) -> TicketSlot:
        """The slot for the admin-only ticket filed when this request's storage job fails
        (file_storage_failure_ticket)."""
        return TicketSlot(
            correlation_id=storage_failure_correlation_id(self.correlation_id),
            what=f"the storage job for {self.what}",
            kind=FiledTicket.Kind.STORAGE_FAILURE,
            subjects=self.subjects,
            table=self.table,
        )


# -- Correlation ids and slots, by kind ----------------------------------------------------------


def allocation_correlation_id(allocation_pk) -> str:
    return f"coldfront:allocation:{allocation_pk}"


def change_correlation_id(allocation_change_pk) -> str:
    return f"coldfront:allocation-change:{allocation_change_pk}"


STORAGE_FAILURE_SUFFIX = ":storage-failed"


def storage_failure_correlation_id(request_correlation_id) -> str:
    return f"{request_correlation_id}{STORAGE_FAILURE_SUFFIX}"


def project_users_correlation_id(project_pk) -> str:
    return f"coldfront:project-users:{project_pk}:{timezone.localdate().isoformat()}"


def project_users_slot(project) -> TicketSlot:
    """The ticket for today's changes to `project`'s users."""
    return TicketSlot(
        correlation_id=project_users_correlation_id(project.pk),
        what=f"user changes on project {project.pk}",
        kind=FiledTicket.Kind.PROJECT_USERS,
        subjects={"project": project},
    )


def hpc_account_correlation_id(username) -> str:
    return f"coldfront:hpc-account:{username}"


def hpc_account_slot(user) -> TicketSlot:
    return TicketSlot(
        correlation_id=hpc_account_correlation_id(user.username),
        what=f"HPC account {user.username}",
        kind=FiledTicket.Kind.HPC_ACCOUNT,
        subjects={"user": user},
    )
