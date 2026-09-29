"""Signal receivers that turn ColdFront domain events into ServiceNow incident tasks.

NOT YET IMPLEMENTED. Imported by TicketingConfig.ready() when TICKETING_ENABLE_SIGNALS is set.

All four signals below already exist in coldfront.core.allocation.signals; sender classes come from
coldfront.core.allocation.views. The requestor_pk kwarg does not exist yet -- it lands in the
companion core PR (docs/plan.md, A1). approver_pk is an optional addition (A3).

Receivers to implement, one per event:

    allocation_new              sender=AllocationCreateView        allocation_pk, requestor_pk
        -> create an incident for the allocation
    allocation_activate         sender=AllocationDetailView        allocation_pk, [approver_pk]
        -> resolve the allocation's incident
    allocation_change_created   sender=AllocationChangeView        allocation_pk,
                                                                   allocation_change_pk, requestor_pk
        -> create an incident for the change request
    allocation_change_approved  sender=AllocationChangeDetailView  allocation_pk,
                                                                   allocation_change_pk, [approver_pk]
        -> resolve the change request's incident

Denials, allocation renewal and account upgrade requests are out of scope (see docs/plan.md).

Follow the in-tree pattern in coldfront/plugins/freeipa/signals.py: a thin @receiver that does
nothing but hand off to a task. Each receiver must:

1. Hand off with ticketing.dispatch.enqueue_ticket, never async_task directly. It skips the task
   when the acting admin has switched ticketing off, waits for transaction.on_commit(), and logs
   rather than raising if Redis is down (an unguarded async_task would 500 the user's POST).
2. Pass only pks. The task re-queries, so it sees committed state rather than whatever the view held.
"""
