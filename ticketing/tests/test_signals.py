import pytest
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
from coldfront.core.test_helpers import factories as f
from simple_history.models import HistoricalRecords

from ticketing.tests.test_allocation_updates import (
    make_cluster_allocation,
    make_storage_allocation,
    make_storage_change,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def committed(django_capture_on_commit_callbacks):
    """`with committed():` runs what enqueue_ticket deferred to transaction.on_commit as the block ends."""
    return lambda: django_capture_on_commit_callbacks(execute=True)


def approve(change):
    change.status = f.AllocationChangeStatusChoiceFactory(name="Approved")
    change.save()
    return change


def set_state(allocation, name, value):
    """Save a storage-plugin state attribute the way coldfront_utils' update_allocation_attribute_value does."""
    attr_type = f.AllocationAttributeTypeFactory(name=name, attribute_type=f.AAttributeTypeFactory(name="Text"))
    attribute = allocation.allocationattribute_set.filter(allocation_attribute_type=attr_type).first()
    if attribute:
        attribute.value = value
        attribute.save()
    else:
        f.AllocationAttributeFactory(allocation=allocation, allocation_attribute_type=attr_type, value=value)


# -- Requests open tickets ----------------------------------------------------------------------


def test_new_allocation_opens_a_ticket_for_the_logged_in_requestor(pi, requestor, in_request, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    in_request(requestor)

    with committed():
        allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk)

    assert queued == [("ticketing.tasks.create_allocation_ticket", (allocation.pk,), {"requestor_pk": requestor.pk})]


def test_requestor_pk_from_core_wins_over_the_logged_in_user(pi, requestor, admin, in_request, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    in_request(admin)

    with committed():
        allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk, requestor_pk=requestor.pk)

    assert queued[0][2] == {"requestor_pk": requestor.pk}


def test_outside_a_request_there_is_no_requestor(pi, queued, committed):
    allocation, _ = make_storage_allocation(pi)

    with committed():
        allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk)

    assert queued == [("ticketing.tasks.create_allocation_ticket", (allocation.pk,), {"requestor_pk": None})]


def test_other_senders_are_ignored(pi, queued, committed):
    allocation, _ = make_storage_allocation(pi)

    with committed():
        allocation_new.send(sender=object, allocation_pk=allocation.pk)

    assert queued == []


def test_change_request_opens_a_ticket(pi, requestor, in_request, queued, committed):
    change = make_storage_change(pi)
    in_request(requestor)

    with committed():
        allocation_change_created.send(
            sender=AllocationChangeView, allocation_pk=change.allocation.pk, allocation_change_pk=change.pk
        )

    assert queued == [("ticketing.tasks.create_allocation_change_ticket", (change.pk,), {"requestor_pk": requestor.pk})]


def test_admin_with_ticketing_off_opens_nothing(pi, admin, in_request, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    in_request(admin, off=True)

    with committed():
        allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk)

    assert queued == []


# -- Storage success resolves them --------------------------------------------------------------


def test_share_created_resolves_the_allocation_ticket(pi, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    with committed():
        set_state(allocation, "share_creation_state", "pending")
    assert queued == []

    with committed():
        set_state(allocation, "share_creation_state", "success")

    assert queued == [("ticketing.tasks.resolve_allocation_ticket", (allocation.pk,), {})]


@pytest.mark.parametrize("value", ["pending", "failed: missing information"])
def test_share_not_created_resolves_nothing(pi, queued, committed, value):
    allocation, _ = make_storage_allocation(pi)

    with committed():
        set_state(allocation, "share_creation_state", value)

    assert queued == []


def test_quota_set_resolves_the_change_request_that_asked_for_it(pi, queued, committed):
    change = approve(make_storage_change(pi, old="10", new="20"))

    with committed():
        set_state(change.allocation, "quota_update_state", "success")

    assert queued == [("ticketing.tasks.resolve_allocation_change_ticket", (change.pk,), {})]


def test_quota_set_picks_the_approved_request_for_the_value_now_in_place(pi, queued, committed):
    change = approve(make_storage_change(pi, old="10", new="20"))
    quota = change.allocationattributechangerequest_set.get().allocation_attribute
    # An earlier approved request for another value, and a pending one for this value.
    earlier = approve(f.AllocationChangeRequestFactory(allocation=change.allocation))
    f.AllocationAttributeChangeRequestFactory(
        allocation_change_request=earlier, allocation_attribute=quota, new_value="15"
    )
    pending = f.AllocationChangeRequestFactory(allocation=change.allocation)
    f.AllocationAttributeChangeRequestFactory(
        allocation_change_request=pending, allocation_attribute=quota, new_value="20"
    )

    with committed():
        set_state(change.allocation, "quota_update_state", "success")

    assert queued == [("ticketing.tasks.resolve_allocation_change_ticket", (change.pk,), {})]


def test_quota_edited_directly_resolves_nothing(pi, queued, committed, caplog):
    allocation, _ = make_storage_allocation(pi)

    with caplog.at_level("INFO", logger="ticketing.signals"), committed():
        set_state(allocation, "quota_update_state", "success")

    assert queued == []
    assert "no approved change request" in caplog.text


def test_other_attributes_saved_as_success_are_ignored(pi, queued, committed):
    allocation, _ = make_storage_allocation(pi)

    with committed():
        set_state(allocation, "something_else_state", "success")

    assert queued == []


# -- An admin with ticketing off approves: the resolve is skipped --------------------------------


def markers(allocation):
    return list(
        allocation.allocationattribute_set.filter(allocation_attribute_type__name="ticketing_suppressed").values_list(
            "value", flat=True
        )
    )


def test_approval_with_ticketing_off_marks_the_request(pi, admin, in_request, committed):
    allocation, _ = make_storage_allocation(pi)
    in_request(admin, off=True)

    allocation_activate.send(sender=AllocationDetailView, allocation_pk=allocation.pk)

    assert markers(allocation) == [f"coldfront:allocation:{allocation.pk}"]
    marker = allocation.allocationattribute_set.get(allocation_attribute_type__name="ticketing_suppressed")
    assert marker.allocation_attribute_type.is_private


@pytest.mark.parametrize("who", ["admin with ticketing on", "non-admin", "no request"])
def test_approval_otherwise_marks_nothing(pi, admin, requestor, in_request, who):
    allocation, _ = make_storage_allocation(pi)
    if who == "admin with ticketing on":
        in_request(admin)
    elif who == "non-admin":
        in_request(requestor, off=True)

    allocation_activate.send(sender=AllocationDetailView, allocation_pk=allocation.pk)

    assert markers(allocation) == []


def test_share_created_after_an_approval_with_ticketing_off_resolves_nothing(pi, admin, in_request, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    in_request(admin, off=True)
    allocation_activate.send(sender=AllocationDetailView, allocation_pk=allocation.pk)
    del HistoricalRecords.context.request  # the storage plugin's background task has no request

    with committed():
        set_state(allocation, "share_creation_state", "success")

    assert queued == []
    assert markers(allocation) == []  # used up


def test_quota_set_after_an_approval_with_ticketing_off_resolves_nothing(pi, admin, in_request, queued, committed):
    change = approve(make_storage_change(pi, old="10", new="20"))
    in_request(admin, off=True)
    allocation_change_approved.send(
        sender=AllocationChangeDetailView, allocation_pk=change.allocation.pk, allocation_change_pk=change.pk
    )
    del HistoricalRecords.context.request

    with committed():
        set_state(change.allocation, "quota_update_state", "success")

    assert queued == []
    assert markers(change.allocation) == []


def test_a_marker_only_skips_its_own_request(pi, admin, in_request, queued, committed):
    change = approve(make_storage_change(pi, old="10", new="20"))
    in_request(admin, off=True)
    allocation_activate.send(sender=AllocationDetailView, allocation_pk=change.allocation.pk)  # marks the allocation
    del HistoricalRecords.context.request

    with committed():
        set_state(change.allocation, "quota_update_state", "success")  # resolves the change request

    assert queued == [("ticketing.tasks.resolve_allocation_change_ticket", (change.pk,), {})]
    assert markers(change.allocation) == [f"coldfront:allocation:{change.allocation.pk}"]


def test_a_marker_that_cant_be_saved_is_logged_and_the_resolve_goes_ahead(pi, admin, in_request, caplog):
    allocation = make_cluster_allocation(pi)  # no "Text" attribute type exists to build the marker from
    in_request(admin, off=True)

    allocation_activate.send(sender=AllocationDetailView, allocation_pk=allocation.pk)

    assert markers(allocation) == []
    assert "Could not record that ticketing is off" in caplog.text
