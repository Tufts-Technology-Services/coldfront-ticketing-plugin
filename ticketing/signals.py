"""Signal receivers that turn ColdFront domain events into ticket-creation tasks.

NOT YET IMPLEMENTED. Imported by TicketingConfig.ready() when TICKETING_ENABLE_SIGNALS is set.

Two of the four signals below (allocation_renewed, user_upgrade_requested) and the requestor_pk
kwarg do not exist in ColdFront yet -- they land in the companion core PR. This module cannot be
imported until then.

Receivers to implement, one per event:

    allocation_new             sender=AllocationCreateView   allocation_pk, requestor_pk
    allocation_renewed         sender=AllocationRenewView    allocation_pk, requestor_pk
    allocation_change_created  sender=AllocationChangeView   allocation_pk, allocation_change_pk,
                                                             requestor_pk
    user_upgrade_requested     sender=UserUpgradeAccount     user_pk

Signals come from coldfront.core.allocation.signals and coldfront.core.user.signals; sender classes
from the corresponding views modules. Follow the in-tree pattern in
coldfront/plugins/freeipa/signals.py: a thin @receiver that does nothing but hand off to a task.

Three things each receiver must do:

1. Wrap the handoff in transaction.on_commit(). No ColdFront view involved is inside an atomic block
   today and ATOMIC_REQUESTS is unset, so this is currently a no-op -- but it costs nothing and
   prevents a stale-read if anyone later wraps those views.
2. Wrap async_task in try/except Exception and log. django-q2 is configured against Redis with no
   orm/sync fallback, so an unguarded async_task raises inside the user's POST and 500s allocation
   submission when Redis is down.
3. Pass the pk through to the task so it can go in the ticket subject, letting a re-delivered task
   thread onto the existing ticket rather than opening a second one.
"""
