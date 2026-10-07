"""Signal receivers that queue ticketing tasks for ColdFront's allocation events.

Imported by TicketingConfig.ready() when TICKETING_ENABLE_SIGNALS is set.

    event                                                  task
    allocation_new (AllocationCreateView)                  create_allocation_ticket
    allocation_change_created (AllocationChangeView)       create_allocation_change_ticket
    share_creation_state saved as "success"                resolve_allocation_ticket
    quota_update_state saved as "success"                  resolve_allocation_change_ticket

Requests open a ticket when they're submitted. They're resolved when the storage plugin reports its
work done, not on approval (decided 2026-10-07): the plugin creates shares and sets quotas in
background tasks after approval and records the outcome in those two allocation attributes, so
resolving on approval could tell the customer "done" before -- or despite -- a failure. A failure
leaves the ticket open for an admin.

Approval (allocation_activate, allocation_change_approved) queues nothing, but it is the last moment
the approving admin's request -- and so their ticketing switch -- is visible. If the switch is off,
the approval leaves a `ticketing_suppressed` marker on the allocation, holding the request's
correlation_id; the storage success that follows finds it, deletes it and resolves nothing.

Each receiver hands off with ticketing.dispatch.enqueue_ticket, never async_task directly, and
passes only pks so the task re-queries committed state. Denials, renewals and account upgrades are
out of scope (see docs/plan.md).
"""

import logging

from coldfront.core.allocation.models import AllocationAttribute, AllocationAttributeChangeRequest
from coldfront.core.allocation.signals import (
    allocation_activate,
    allocation_change_approved,
    allocation_change_created,
    allocation_new,
)
from coldfront.core.allocation.views import (
    AllocationChangeDetailView,
    AllocationChangeView,
    AllocationCreateView,
    AllocationDetailView,
)
from coldfront.core.utils.common import import_from_settings
from coldfront_utils.attribute.attribute import create_allocation_attribute_type
from django.db.models.signals import post_save
from django.dispatch import receiver

from ticketing import toggle
from ticketing.allocation_updates import QUOTA_ATTRIBUTE_NAME
from ticketing.dispatch import enqueue_ticket
from ticketing.tasks import allocation_correlation_id, change_correlation_id

logger = logging.getLogger(__name__)

# The storage plugin's outcome attributes (its SHARE_CREATION_STATE_ATTRIBUTE_NAME and
# QUOTA_UPDATE_STATE_ATTRIBUTE_NAME). Both are configurable there, so they are here too.
SHARE_STATE_ATTRIBUTE = import_from_settings("TICKETING_SHARE_STATE_ATTRIBUTE", "share_creation_state")
QUOTA_STATE_ATTRIBUTE = import_from_settings("TICKETING_QUOTA_STATE_ATTRIBUTE", "quota_update_state")
SUCCESS = "success"
# Private allocation attribute: "an admin approved this with ticketing off; don't resolve it".
SUPPRESSED_ATTRIBUTE = "ticketing_suppressed"


def requestor_pk(kwargs):
    """Who submitted the request: `requestor_pk` if core sends it, else the logged-in user.

    Core doesn't send requestor_pk yet (docs/plan.md, A1). These signals are sent from inside the
    view, so the request HistoryRequestMiddleware stashed is the submitter's. Without either the
    task falls back to the PI.
    """
    if kwargs.get("requestor_pk"):
        return kwargs["requestor_pk"]
    user = getattr(toggle.current_request(), "user", None)
    return user.pk if user is not None and user.is_authenticated else None


# -- Requests open tickets ----------------------------------------------------------------------


@receiver(allocation_new, sender=AllocationCreateView, dispatch_uid="ticketing-allocation-new")
def allocation_requested(sender, allocation_pk, **kwargs):
    enqueue_ticket("ticketing.tasks.create_allocation_ticket", allocation_pk, requestor_pk=requestor_pk(kwargs))


@receiver(allocation_change_created, sender=AllocationChangeView, dispatch_uid="ticketing-allocation-change-created")
def allocation_change_requested(sender, allocation_pk, allocation_change_pk, **kwargs):
    enqueue_ticket(
        "ticketing.tasks.create_allocation_change_ticket", allocation_change_pk, requestor_pk=requestor_pk(kwargs)
    )


# -- Approval records an admin's switch; storage success resolves -------------------------------


@receiver(allocation_activate, sender=AllocationDetailView, dispatch_uid="ticketing-allocation-activate")
def allocation_approved(sender, allocation_pk, **kwargs):
    suppress_if_off(allocation_pk, allocation_correlation_id(allocation_pk))


@receiver(allocation_change_approved, sender=AllocationChangeDetailView, dispatch_uid="ticketing-change-approved")
def allocation_change_approved_(sender, allocation_pk, allocation_change_pk, **kwargs):
    suppress_if_off(allocation_pk, change_correlation_id(allocation_change_pk))


def suppress_if_off(allocation_pk, correlation_id):
    """If the approving admin has ticketing off, mark the request so its resolve is skipped.

    Runs inside the admin's approval, so a failure is logged rather than raised -- and then the
    resolve goes ahead, which is the safe direction.
    """
    request = toggle.current_request()
    if request is None or not toggle.is_off(request):
        return
    try:
        AllocationAttribute.objects.create(
            allocation_id=allocation_pk,
            allocation_attribute_type=create_allocation_attribute_type(SUPPRESSED_ATTRIBUTE, "Text", is_private=True),
            value=correlation_id,
        )
    except Exception:
        logger.exception("Could not record that ticketing is off for %s; it will be resolved", correlation_id)
        return
    logger.info("Ticketing is off for %s; %s won't be resolved", request.user.username, correlation_id)


def take_suppression(allocation_pk, correlation_id) -> bool:
    """Delete the request's suppression marker, returning whether there was one."""
    deleted, _ = AllocationAttribute.objects.filter(
        allocation_id=allocation_pk, allocation_attribute_type__name=SUPPRESSED_ATTRIBUTE, value=correlation_id
    ).delete()
    return bool(deleted)


@receiver(post_save, sender=AllocationAttribute, dispatch_uid="ticketing-storage-state")
def storage_state_saved(sender, instance, **kwargs):
    """Resolve the request whose storage work just succeeded, unless its approver had ticketing off.

    Fires on every allocation attribute save, so it bails on the value before touching the database.
    The storage plugin only saves a state when it changes, so each success arrives once.
    """
    if instance.value.strip() != SUCCESS:
        return
    name = instance.allocation_attribute_type.name
    if name == SHARE_STATE_ATTRIBUTE:
        task, pk, correlation_id = (
            "ticketing.tasks.resolve_allocation_ticket",
            instance.allocation_id,
            allocation_correlation_id(instance.allocation_id),
        )
    elif name == QUOTA_STATE_ATTRIBUTE:
        change_pk = applied_quota_change_pk(instance.allocation_id)
        if change_pk is None:
            logger.info("Quota set on allocation %s with no approved change request for it", instance.allocation_id)
            return
        task, pk, correlation_id = (
            "ticketing.tasks.resolve_allocation_change_ticket",
            change_pk,
            change_correlation_id(change_pk),
        )
    else:
        return
    if take_suppression(instance.allocation_id, correlation_id):
        logger.info("Not resolving %s: approved with ticketing off", correlation_id)
        return
    enqueue_ticket(task, pk)


def applied_quota_change_pk(allocation_pk):
    """The approved change request that set the allocation's current quota, or None.

    The newest approved request whose requested quota is the value now in place. None when the quota
    was edited directly (AllocationAttributeEditView), which has no change request or ticket.
    """
    quota = AllocationAttribute.objects.filter(
        allocation_id=allocation_pk, allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME
    ).first()
    if quota is None:
        return None
    return (
        AllocationAttributeChangeRequest.objects.filter(
            allocation_attribute=quota,
            allocation_change_request__status__name="Approved",
            new_value=quota.value,
        )
        .order_by("-allocation_change_request__modified", "-allocation_change_request__pk")
        .values_list("allocation_change_request_id", flat=True)
        .first()
    )
