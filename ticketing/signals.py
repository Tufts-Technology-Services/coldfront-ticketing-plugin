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

1. Wrap the handoff in transaction.on_commit(). No ColdFront view involved is inside an atomic block
   today and ATOMIC_REQUESTS is unset, so this is currently a no-op -- but it costs nothing and
   prevents a stale-read if anyone later wraps those views.
2. Wrap async_task in try/except Exception and log. django-q2 is configured against Redis with no
   orm/sync fallback, so an unguarded async_task raises inside the user's POST and 500s allocation
   submission when Redis is down.
3. Pass only pks. The task re-queries, so it sees committed state rather than whatever the view held.
"""
