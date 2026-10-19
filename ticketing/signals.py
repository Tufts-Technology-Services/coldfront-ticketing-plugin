"""Signal receivers that queue ticketing tasks for ColdFront's allocation events.

Imported by TicketingConfig.ready() when TICKETING_ENABLE_SIGNALS is set.

    event                                                  task
    allocation_new (AllocationCreateView)                  create_allocation_ticket
    allocation_change_created (AllocationChangeView)       create_allocation_change_ticket
    share_creation_successful                              resolve_allocation_ticket
    quota_update_successful                                resolve_allocation_change_ticket
    allocation_activate, if storage won't provision        resolve_allocation_ticket
    allocation_change_approved, if storage won't apply it  resolve_allocation_change_ticket
    share_creation_failed, quota_update_failed             file_storage_failure_ticket
    either success, after a failure                        resolve_storage_failure_ticket
    project_activate_user, project_remove_user (any sender)  create_project_users_ticket
    hpc_account_requested (account signup, no UID yet)     create_hpc_account_ticket

Requests open a ticket when they're submitted, unless a ticketing admin submitted them. Requests the
storage plugin provisions are resolved when it reports its work done, not on approval (decided
2026-10-07): the plugin creates shares and sets quotas in background tasks after approval, and
sends the outcome as coldfront_utils.signals' share_creation_successful/_failed and
quota_update_successful/_failed, so resolving on approval could tell the customer "done" before --
or despite -- a failure. A failure leaves the request's ticket open, and
files an admin-only child ticket for it, resolved by a later success. Everything else -- other
resource types, and Storage change requests that don't change the quota -- is resolved on approval
(storage_will_provision decides; 2026-10-15).

For a request storage provisions, approval queues nothing, but it is the last moment the approving
admin's request -- and so their ticketing switch -- is visible. If the switch is off, the approval
leaves a `ticketing_suppressed` marker on the allocation, holding the request's correlation_id; the
storage success that follows finds it, deletes it and resolves nothing. A failure in between finds
it too, leaves it, and files nothing. A request resolved on approval needs no marker: the resolve is
queued inside the admin's request, where enqueue_ticket sees the switch.

Each receiver hands off with ticketing.dispatch.enqueue_ticket, never async_task directly, and
passes only pks so the task re-queries committed state. Denials, renewals and account upgrades are
out of scope (see "Out of scope" in docs/design.md).
"""

import logging

from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttribute,
    AllocationAttributeChangeRequest,
    AllocationChangeRequest,
)
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
from coldfront.core.project.models import ProjectUser
from coldfront.core.project.signals import project_activate_user, project_remove_user
from coldfront_utils.attribute.attribute import create_allocation_attribute_type
from coldfront_utils.signals import (
    hpc_account_requested,
    quota_update_failed,
    quota_update_successful,
    share_creation_failed,
    share_creation_successful,
)
from django.contrib.auth import get_user_model
from django.dispatch import receiver

from ticketing import toggle
from ticketing.allocation_updates import QUOTA_ATTRIBUTE_NAME, storage_will_provision
from ticketing.dispatch import enqueue_ticket
from ticketing.models import FiledTicket
from ticketing.permissions import is_ticketing_admin
from ticketing.slots import allocation_correlation_id, change_correlation_id, storage_failure_correlation_id

logger = logging.getLogger(__name__)

# Private allocation attribute: "an admin approved this with ticketing off; don't resolve it".
SUPPRESSED_ATTRIBUTE = "ticketing_suppressed"


def requestor(kwargs):
    """Who submitted the request: `requestor_pk` if core sends it, else the logged-in user.

    Core doesn't send requestor_pk yet (see "Companion ColdFront change" in docs/design.md). These signals are sent from inside the
    view, so the request HistoryRequestMiddleware stashed is the submitter's. Without either the
    task falls back to the PI.
    """
    if kwargs.get("requestor_pk"):
        return get_user_model().objects.filter(pk=kwargs["requestor_pk"]).first()
    user = getattr(toggle.current_request(), "user", None)
    return user if user is not None and user.is_authenticated else None


def requestor_pk(kwargs):
    return kwargs.get("requestor_pk") or getattr(requestor(kwargs), "pk", None)


def sent_by(sender, view) -> bool:
    """Whether a signal was sent by `view` or a subclass of it.

    The view receivers take every sender and check with this, rather than passing `sender=` to
    @receiver. Plugins replace core views with subclasses: the storage plugin swaps in its own
    AllocationCreateView in its ready(), and the URLs then send with the subclass. `sender=` matches
    one class exactly, and which class the name held when this module was imported depends on the
    order of INSTALLED_APPS.
    """
    return isinstance(sender, type) and issubclass(sender, view)


# -- Requests open tickets ----------------------------------------------------------------------


@receiver(allocation_new, dispatch_uid="ticketing-allocation-new")
def allocation_requested(sender, allocation_pk, **kwargs):
    if sent_by(sender, AllocationCreateView):
        open_request("ticketing.tasks.create_allocation_ticket", allocation_pk, kwargs)


@receiver(allocation_change_created, dispatch_uid="ticketing-allocation-change-created")
def allocation_change_requested(sender, allocation_pk, allocation_change_pk, **kwargs):
    if sent_by(sender, AllocationChangeView):
        open_request("ticketing.tasks.create_allocation_change_ticket", allocation_change_pk, kwargs)


def open_request(task, pk, kwargs):
    """Queue the request's ticket, unless a ticketing admin (ticketing.permissions) submitted it.

    Admins submit requests on a customer's behalf, usually for one the customer already asked for in
    ServiceNow. A ticket opened now would email the PI and Approvers about a second ticket before
    there's a request to link the original to (decided 2026-10-15). The resolve still goes out: it
    resolves the ticket an admin linked, or with none, files one already resolved.
    """
    user = requestor(kwargs)
    if is_ticketing_admin(user):
        logger.info("Not opening a ticket for %s %s: requested by a ticketing admin (%s)", task, pk, user.username)
        return
    enqueue_ticket(task, pk, requestor_pk=getattr(user, "pk", None))


# -- Approval records an admin's switch; storage success resolves -------------------------------


@receiver(allocation_activate, dispatch_uid="ticketing-allocation-activate")
def allocation_approved(sender, allocation_pk, **kwargs):
    if not sent_by(sender, AllocationDetailView):
        return
    if storage_will_provision(Allocation.objects.get(pk=allocation_pk)):
        suppress_if_off(allocation_pk, allocation_correlation_id(allocation_pk))
    else:
        enqueue_ticket("ticketing.tasks.resolve_allocation_ticket", allocation_pk)


@receiver(allocation_change_approved, dispatch_uid="ticketing-change-approved")
def allocation_change_approved_(sender, allocation_pk, allocation_change_pk, **kwargs):
    if not sent_by(sender, AllocationChangeDetailView):
        return
    change = AllocationChangeRequest.objects.select_related("allocation").get(pk=allocation_change_pk)
    if storage_will_provision(change.allocation, change):
        suppress_if_off(allocation_pk, change_correlation_id(allocation_change_pk))
    else:
        enqueue_ticket("ticketing.tasks.resolve_allocation_change_ticket", allocation_change_pk)


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


def _markers(allocation_pk, correlation_id):
    return AllocationAttribute.objects.filter(
        allocation_id=allocation_pk, allocation_attribute_type__name=SUPPRESSED_ATTRIBUTE, value=correlation_id
    )


def take_suppression(allocation_pk, correlation_id) -> bool:
    """Delete the request's suppression marker, returning whether there was one."""
    deleted, _ = _markers(allocation_pk, correlation_id).delete()
    return bool(deleted)


def is_suppressed(allocation_pk, correlation_id) -> bool:
    """Whether the request has a suppression marker, leaving it for the success that uses it up."""
    return _markers(allocation_pk, correlation_id).exists()


@receiver(share_creation_successful, dispatch_uid="ticketing-share-created")
def share_created(sender, allocation, **kwargs):
    storage_job_succeeded(allocation.pk, quota=False)


@receiver(quota_update_successful, dispatch_uid="ticketing-quota-set")
def quota_set(sender, allocation, **kwargs):
    storage_job_succeeded(allocation.pk, quota=True)


@receiver(share_creation_failed, dispatch_uid="ticketing-share-failed")
def share_failed(sender, allocation, message="", **kwargs):
    storage_job_failed(allocation.pk, message, quota=False)


@receiver(quota_update_failed, dispatch_uid="ticketing-quota-failed")
def quota_failed(sender, allocation, message="", **kwargs):
    storage_job_failed(allocation.pk, message, quota=True)


def storage_job_request(allocation_pk, *, quota):
    """The request a storage job carried out: (change_pk, correlation_id, resolve task, its pk), or None.

    A share was created for the allocation's own request; a quota set, for the approved change request
    that asked for the quota now in place. None when there's no such request, as for a quota edited
    directly (AllocationAttributeEditView), which has no ticket.
    """
    if not quota:
        return (
            None,
            allocation_correlation_id(allocation_pk),
            "ticketing.tasks.resolve_allocation_ticket",
            allocation_pk,
        )
    change_pk = applied_quota_change_pk(allocation_pk)
    if change_pk is None:
        logger.info("Quota set on allocation %s with no approved change request for it", allocation_pk)
        return None
    return change_pk, change_correlation_id(change_pk), "ticketing.tasks.resolve_allocation_change_ticket", change_pk


def storage_job_succeeded(allocation_pk, *, quota):
    """Resolve the request, and its job's failure ticket if one is open, unless its approver had
    ticketing off. The storage plugin sends one success per job."""
    request = storage_job_request(allocation_pk, quota=quota)
    if request is None:
        return
    change_pk, correlation_id, resolve, pk = request
    if take_suppression(allocation_pk, correlation_id):
        logger.info("Not resolving %s: approved with ticketing off", correlation_id)
        return
    if FiledTicket.objects.filter(
        correlation_id=storage_failure_correlation_id(correlation_id), resolved_at__isnull=True
    ).exists():
        enqueue_ticket("ticketing.tasks.resolve_storage_failure_ticket", allocation_pk, change_pk)
    enqueue_ticket(resolve, pk)


def storage_job_failed(allocation_pk, message, *, quota):
    """File an admin-only failure ticket, a child of the request's, unless its approver had ticketing
    off. Sent once per failed attempt; a repeat adds a work note to the same ticket."""
    request = storage_job_request(allocation_pk, quota=quota)
    if request is None:
        return
    change_pk, correlation_id, _, _ = request
    if is_suppressed(allocation_pk, correlation_id):
        logger.info("Not filing a storage failure for %s: approved with ticketing off", correlation_id)
        return
    # shown on the ticket as the job's state, in the "failed: <reason>" form earlier storage versions wrote
    state = f"failed: {message}" if message else "failed"
    enqueue_ticket("ticketing.tasks.file_storage_failure_ticket", allocation_pk, change_pk, state=state)


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


# -- HPC accounts: opened while one waits on a UID -----------------------------------------------


@receiver(hpc_account_requested, dispatch_uid="ticketing-hpc-account-requested")
def open_hpc_account_ticket(sender, username, **kwargs):
    """Tell the user their HPC account request was received: an account was requested and isn't ready yet.

    Sent once per account. Today's sender is the account signup plugin, from its worker, only when
    the user had no UID and one has just been created; there's no request, so no ticketing switch to
    consult. Resolving the ticket once the account exists isn't wired up yet.
    """
    enqueue_ticket("ticketing.tasks.create_hpc_account_ticket", username)


# -- Project users: one notice per project, per day ---------------------------------------------


@receiver(project_activate_user, dispatch_uid="ticketing-project-user-added")
@receiver(project_remove_user, dispatch_uid="ticketing-project-user-removed")
def project_users_changed(sender, project_user_pk, **kwargs):
    """Queue the project's notice, once per project per request.

    ColdFront sends one signal per user added or removed, so changing five users in one form sends
    five. The task's per-day correlation_id stops a second ticket only once the first is filed, and
    five tasks queued together could each look before any had filed; so within a request only the
    first is queued. Ticketing admins (ticketing.permissions) being added or removed aren't members to
    notify, and queue nothing. Neither does the PI: Tufts' project-create view sends project_activate_user
    for the new project's PI, and a PI is never otherwise added or removed, so it would only ever
    tell a new project's PI that its users had changed.
    """
    project_user = ProjectUser.objects.select_related("user", "project").get(pk=project_user_pk)
    if is_ticketing_admin(project_user.user) or project_user.user_id == project_user.project.pi_id:
        return
    if not first_in_request(("project-users", project_user.project_id)):
        return
    enqueue_ticket(
        "ticketing.tasks.create_project_users_ticket", project_user.project_id, requestor_pk=requestor_pk(kwargs)
    )


def first_in_request(key) -> bool:
    """True the first time `key` is seen during the current request, and always outside one."""
    request = toggle.current_request()
    if request is None:
        return True
    seen = request.__dict__.setdefault("_ticketing_queued", set())
    if key in seen:
        return False
    seen.add(key)
    return True
