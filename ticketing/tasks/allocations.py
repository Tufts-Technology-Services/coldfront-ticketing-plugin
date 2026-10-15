"""Allocation requests and allocation change requests: opened on submission, resolved when done.

Opened by allocation_new / allocation_change_created; resolved on storage success, or on approval
when the storage plugin won't provision the request (see ticketing.signals). The message depends on
the allocation's resource (ticketing.allocation_updates).
"""

from ticketing.allocation_updates import (
    describe_activation,
    describe_change,
    describe_new_allocation,
    describe_request,
)
from ticketing.client import IncidentRef
from ticketing.slots import (
    AllocationTicketSlot,
)
from ticketing.tasks.filing import (
    open_ticket,
    resolve_or_file,
)
from ticketing.tasks.people import (
    admin_request_approver,
    people_fields,
)


def create_allocation_ticket(
    allocation_pk: int, requestor_pk: int | None = None, affected_pk: int | None = None
) -> IncidentRef:
    """Open an incident for a newly requested allocation.

    Every allocation request gets one (see describe_new_allocation): once core's
    ticket emails are gated off it is the only ticket admins get. resolve_allocation_ticket finds it
    by the shared correlation_id "coldfront:allocation:<pk>". Opened with no state, so the instance
    starts it Active. The requestor is the contact person, and `affected_pk` the affected client if
    it isn't them (see people_fields). Safe to re-run.
    """
    slot = AllocationTicketSlot.load(allocation_pk)
    return open_ticket(
        slot,
        people=lambda: people_fields(slot.allocation.project, requestor_pk, affected_pk),
        describe=lambda requestor: describe_new_allocation(slot.allocation, requestor),
    )


def resolve_allocation_ticket(
    allocation_pk: int, requestor_pk: int | None = None, affected_pk: int | None = None
) -> IncidentRef | None:
    """Tell the requester their allocation is active, and close out its ticket.

    The message depends on the allocation's resource (ticketing.allocation_updates): Storage's, or
    the generic one; an allocation with no resource is left open for an admin. Queued on storage
    success, or on activation when storage won't provision it. With no incident to resolve, a
    new one is filed already resolved. `requestor_pk` and `affected_pk` only matter then -- without
    them the PI is the caller. If a ticketing admin submitted the request, it is assigned to the admin who
    activated it (admin_request_approver). Safe to re-run.
    """
    slot = AllocationTicketSlot.load(allocation_pk)
    return resolve_or_file(
        slot,
        people=lambda: people_fields(slot.allocation.project, requestor_pk, affected_pk),
        describe=lambda: describe_activation(slot.allocation),
        assignee=lambda: admin_request_approver(slot.request, slot.approved_status),
    )


def create_allocation_change_ticket(
    allocation_change_pk: int, requestor_pk: int | None = None, affected_pk: int | None = None
) -> IncidentRef:
    """Open an incident for a newly submitted allocation change request.

    Every change request gets one (see describe_request); resolve_allocation_change_ticket later
    finds it by the shared correlation_id "coldfront:allocation-change:<pk>". Opened with no state, so the
    instance starts it Active. The requestor is the contact person, and `affected_pk` the affected
    client if it isn't them (see people_fields). Safe to re-run.
    """
    slot = AllocationTicketSlot.load(None, allocation_change_pk)
    return open_ticket(
        slot,
        people=lambda: people_fields(slot.allocation.project, requestor_pk, affected_pk),
        describe=lambda requestor: describe_request(slot.request, requestor),
    )


def resolve_allocation_change_ticket(
    allocation_change_pk: int, requestor_pk: int | None = None, affected_pk: int | None = None
) -> IncidentRef | None:
    """Tell the requester their approved allocation change has been made, and close out its ticket.

    The message depends on the allocation's resource (ticketing.allocation_updates): Storage's for
    a quota change, else the generic one listing what changed; an allocation with no resource is left
    open for an admin. Queued on storage success, or on approval when storage won't apply it. With no
    incident to resolve, a new one is filed already resolved. `requestor_pk` and `affected_pk` only
    matter then -- without them the PI is the caller. If a ticketing admin submitted the request, it
    is assigned to the admin who approved it (admin_request_approver). Safe to re-run.
    """
    slot = AllocationTicketSlot.load(None, allocation_change_pk)
    return resolve_or_file(
        slot,
        people=lambda: people_fields(slot.allocation.project, requestor_pk, affected_pk),
        describe=lambda: describe_change(slot.request),
        assignee=lambda: admin_request_approver(slot.request, slot.approved_status),
    )
