"""Task functions that create and resolve ServiceNow incidents. Invoked via django_q async_task.

    task                              for                                  correlation_id
    create_hpc_account_ticket         an HPC account waits on a UID *      coldfront:hpc-account:<utln>
    resolve_hpc_account_ticket        an HPC account is created *          coldfront:hpc-account:<utln>
    create_group_membership_ticket    a user is added to a group *         coldfront:group-add:<group>:<utln>:<date>
    create_group_removal_ticket       a user leaves a group *              coldfront:group-remove:<group>:<utln>:<date>
    create_project_users_ticket       project_(activate|remove)_user       coldfront:project-users:<pk>:<date>
    create_allocation_ticket          allocation_new                       coldfront:allocation:<pk>
    resolve_allocation_ticket         share_creation_state = success       coldfront:allocation:<pk>
    create_allocation_change_ticket   allocation_change_created            coldfront:allocation-change:<pk>
    resolve_allocation_change_ticket  quota_update_state = success         coldfront:allocation-change:<pk>
    file_storage_failure_ticket       either state = failed...             <request's>:storage-failed
    resolve_storage_failure_ticket    either state = success, after one    <request's>:storage-failed

    * not triggered yet (see "Not yet wired up" in docs/design.md); the rest are wired up in
      ticketing.signals.

Each task is defined in the module for its kind of ticket, and re-exported here: the signals queue
them by these names ("ticketing.tasks.create_allocation_ticket"), so the names stay put.

    allocations        allocation and change requests
    storage_failures   storage-failure child tickets
    project_users      the daily project user notice
    hpc_accounts       HPC account tickets
    groups             group membership tickets (unused)
    filing             what they're built on: open_ticket, resolve_or_file, record_filed
    people             who is on a ticket, and who approved a request

Each request or event has one TicketSlot (ticketing.slots): the ticket ColdFront keeps for it,
whichever incident fills it.

Each request lifecycle shares one correlation_id, which is how the resolve task finds the incident
the create task opened, and how any task that is re-run finds its own earlier work instead of filing
a duplicate. Each ticket filed, found or resolved is recorded as a FiledTicket (ticketing.models), for
links from ColdFront's pages; tasks that only file check that record before asking ServiceNow.
Resolving still asks ServiceNow, which has the incident's current state. The unused group tasks
record nothing.

Create tasks (open_ticket) file an admin-facing description, and leave state and assigned_to unset
so the incident opens New in the assignment group's queue. Resolve tasks (resolve_or_file) post a
customer-facing message as a comment and resolve, assigning the incident to TICKETING_ASSIGNED_TO if
no one has picked it up; with no incident to resolve they file one already resolved. Allocation
messages are chosen by resource (ticketing.allocation_updates), with a generic message for
resources that have none of their own; only an allocation with no resource is left open for an
admin.
"""

from ticketing.tasks.allocations import (
    create_allocation_change_ticket,
    create_allocation_ticket,
    resolve_allocation_change_ticket,
    resolve_allocation_ticket,
)
from ticketing.tasks.groups import create_group_membership_ticket, create_group_removal_ticket
from ticketing.tasks.hpc_accounts import create_hpc_account_ticket, resolve_hpc_account_ticket
from ticketing.tasks.project_users import create_project_users_ticket
from ticketing.tasks.storage_failures import file_storage_failure_ticket, resolve_storage_failure_ticket

__all__ = [
    "create_allocation_change_ticket",
    "create_allocation_ticket",
    "create_group_membership_ticket",
    "create_group_removal_ticket",
    "create_hpc_account_ticket",
    "create_project_users_ticket",
    "file_storage_failure_ticket",
    "resolve_allocation_change_ticket",
    "resolve_allocation_ticket",
    "resolve_hpc_account_ticket",
    "resolve_storage_failure_ticket",
]
