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
from coldfront_utils.signals import (
    hpc_account_provisioned,
    hpc_account_provisioning_failed,
    hpc_account_requested,
    quota_update_failed,
    quota_update_successful,
    share_creation_failed,
    share_creation_successful,
)
from simple_history.models import HistoricalRecords

from ticketing.models import FiledTicket
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


def storage_succeeded(allocation, *, quota=False):
    """Send what the storage plugin sends when it has created a share (quota=True: set a quota)."""
    signal = quota_update_successful if quota else share_creation_successful
    signal.send(sender=None, allocation=allocation, result={"message": "successful:"})


def storage_failed(allocation, message="", *, quota=False):
    """Send what the storage plugin sends when a share creation (quota=True: quota update) fails."""
    signal = quota_update_failed if quota else share_creation_failed
    signal.send(sender=None, allocation=allocation, message=message, exception=None)


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


class ReplacedCreateView(AllocationCreateView):
    """Like the storage plugin's view, which it swaps in for core's AllocationCreateView."""


def test_a_view_a_plugin_replaced_still_opens_a_ticket(pi, requestor, in_request, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    in_request(requestor)

    with committed():
        allocation_new.send(sender=ReplacedCreateView, allocation_pk=allocation.pk)

    assert queued == [("ticketing.tasks.create_allocation_ticket", (allocation.pk,), {"requestor_pk": requestor.pk})]


def test_change_request_opens_a_ticket(pi, requestor, in_request, queued, committed):
    change = make_storage_change(pi)
    in_request(requestor)

    with committed():
        allocation_change_created.send(
            sender=AllocationChangeView, allocation_pk=change.allocation.pk, allocation_change_pk=change.pk
        )

    assert queued == [("ticketing.tasks.create_allocation_change_ticket", (change.pk,), {"requestor_pk": requestor.pk})]


@pytest.mark.parametrize("who", ["ticketing_admin", "admin"])
def test_ticketing_admins_requests_open_nothing(pi, in_request, queued, committed, request, who):
    allocation, _ = make_storage_allocation(pi)
    change = make_storage_change(pi)
    in_request(request.getfixturevalue(who))

    with committed():
        allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk)
        allocation_change_created.send(
            sender=AllocationChangeView, allocation_pk=change.allocation.pk, allocation_change_pk=change.pk
        )

    assert queued == []


def test_an_admin_requestor_pk_from_core_opens_nothing(pi, requestor, admin, in_request, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    in_request(requestor)

    with committed():
        allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk, requestor_pk=admin.pk)

    assert queued == []


def test_an_admins_request_is_still_resolved(pi, admin, in_request, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    in_request(admin)
    with committed():
        allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk)

    with committed():
        storage_succeeded(allocation)

    assert queued == [("ticketing.tasks.resolve_allocation_ticket", (allocation.pk,), {})]


# -- Storage success resolves them --------------------------------------------------------------


def test_share_created_resolves_the_allocation_ticket(pi, queued, committed):
    allocation, _ = make_storage_allocation(pi)

    with committed():
        storage_succeeded(allocation)

    assert queued == [("ticketing.tasks.resolve_allocation_ticket", (allocation.pk,), {})]


def test_saving_a_state_attribute_resolves_nothing(pi, queued, committed):
    """Only the storage plugin's signals count, not an edit to the attribute it records its state in."""
    allocation, _ = make_storage_allocation(pi)
    state_type = f.AllocationAttributeTypeFactory(
        name="share_creation_state", attribute_type=f.AAttributeTypeFactory(name="Text")
    )

    with committed():
        f.AllocationAttributeFactory(allocation=allocation, allocation_attribute_type=state_type, value="successful")

    assert queued == []


@pytest.mark.parametrize(("message", "state"), [("", "failed"), ("missing information", "failed: missing information")])
def test_a_failed_share_files_a_failure_ticket(pi, queued, committed, message, state):
    allocation, _ = make_storage_allocation(pi)

    with committed():
        storage_failed(allocation, message)

    assert queued == [("ticketing.tasks.file_storage_failure_ticket", (allocation.pk, None), {"state": state})]


def test_a_failed_quota_change_files_a_failure_ticket_for_its_request(pi, queued, committed):
    change = approve(make_storage_change(pi, old="10", new="20"))

    with committed():
        storage_failed(change.allocation, quota=True)

    assert queued == [
        ("ticketing.tasks.file_storage_failure_ticket", (change.allocation.pk, change.pk), {"state": "failed"})
    ]


def test_success_after_a_failure_ticket_resolves_both(pi, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    FiledTicket.objects.create(
        correlation_id=f"coldfront:allocation:{allocation.pk}:storage-failed",
        kind=FiledTicket.Kind.STORAGE_FAILURE,
        number="INC0000002",
        sys_id="i-2",
        allocation=allocation,
    )

    with committed():
        storage_succeeded(allocation)

    assert queued == [
        ("ticketing.tasks.resolve_storage_failure_ticket", (allocation.pk, None), {}),
        ("ticketing.tasks.resolve_allocation_ticket", (allocation.pk,), {}),
    ]


def test_quota_set_resolves_the_change_request_that_asked_for_it(pi, queued, committed):
    change = approve(make_storage_change(pi, old="10", new="20"))

    with committed():
        storage_succeeded(change.allocation, quota=True)

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
        storage_succeeded(change.allocation, quota=True)

    assert queued == [("ticketing.tasks.resolve_allocation_change_ticket", (change.pk,), {})]


def test_a_failed_quota_with_no_change_request_files_nothing(pi, queued, committed):
    """A quota edited directly has no request, so no ticket to be the failure's parent."""
    allocation, _ = make_storage_allocation(pi)

    with committed():
        storage_failed(allocation, "VAST said no", quota=True)

    assert queued == []


def test_quota_edited_directly_resolves_nothing(pi, queued, committed, caplog):
    allocation, _ = make_storage_allocation(pi)

    with caplog.at_level("INFO", logger="ticketing.signals"), committed():
        storage_succeeded(allocation, quota=True)

    assert queued == []
    assert "no approved change request" in caplog.text


# -- What storage doesn't provision resolves on approval -----------------------------------------


def test_approving_a_storage_allocation_waits_for_storage(pi, admin, in_request, queued, committed):
    allocation, _ = make_storage_allocation(pi)
    in_request(admin)

    with committed():
        allocation_activate.send(sender=AllocationDetailView, allocation_pk=allocation.pk)

    assert queued == []


def test_activating_a_non_storage_allocation_resolves_it(pi, admin, in_request, queued, committed):
    allocation = make_cluster_allocation(pi)
    in_request(admin)

    with committed():
        allocation_activate.send(sender=AllocationDetailView, allocation_pk=allocation.pk)

    assert queued == [("ticketing.tasks.resolve_allocation_ticket", (allocation.pk,), {})]
    assert markers(allocation) == []


def test_approving_a_quota_change_waits_for_storage(pi, admin, in_request, queued, committed):
    change = approve(make_storage_change(pi))
    in_request(admin)

    with committed():
        allocation_change_approved.send(
            sender=AllocationChangeDetailView, allocation_pk=change.allocation.pk, allocation_change_pk=change.pk
        )

    assert queued == []


def test_approving_a_change_storage_wont_apply_resolves_it(pi, admin, in_request, queued, committed):
    change = approve(make_storage_change(pi))
    change.allocationattributechangerequest_set.all().delete()  # an end-date extension, say
    in_request(admin)

    with committed():
        allocation_change_approved.send(
            sender=AllocationChangeDetailView, allocation_pk=change.allocation.pk, allocation_change_pk=change.pk
        )

    assert queued == [("ticketing.tasks.resolve_allocation_change_ticket", (change.pk,), {})]


def test_with_ticketing_off_a_non_storage_activation_resolves_nothing_and_leaves_no_marker(
    pi, admin, in_request, queued, committed
):
    allocation = make_cluster_allocation(pi)
    in_request(admin, off=True)

    with committed():
        allocation_activate.send(sender=AllocationDetailView, allocation_pk=allocation.pk)

    assert queued == []
    assert markers(allocation) == []


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
        storage_succeeded(allocation)

    assert queued == []
    assert markers(allocation) == []  # used up


def test_a_failure_after_an_approval_with_ticketing_off_files_nothing_and_keeps_the_marker(
    pi, admin, in_request, queued, committed
):
    allocation, _ = make_storage_allocation(pi)
    in_request(admin, off=True)
    allocation_activate.send(sender=AllocationDetailView, allocation_pk=allocation.pk)
    del HistoricalRecords.context.request

    with committed():
        storage_failed(allocation)
    with committed():
        storage_succeeded(allocation)

    assert queued == []  # the marker outlived the failure, and suppressed the success too


def test_quota_set_after_an_approval_with_ticketing_off_resolves_nothing(pi, admin, in_request, queued, committed):
    change = approve(make_storage_change(pi, old="10", new="20"))
    in_request(admin, off=True)
    allocation_change_approved.send(
        sender=AllocationChangeDetailView, allocation_pk=change.allocation.pk, allocation_change_pk=change.pk
    )
    del HistoricalRecords.context.request

    with committed():
        storage_succeeded(change.allocation, quota=True)

    assert queued == []
    assert markers(change.allocation) == []


def test_a_marker_only_skips_its_own_request(pi, admin, in_request, queued, committed):
    change = approve(make_storage_change(pi, old="10", new="20"))
    in_request(admin, off=True)
    allocation_activate.send(sender=AllocationDetailView, allocation_pk=change.allocation.pk)  # marks the allocation
    del HistoricalRecords.context.request

    with committed():
        storage_succeeded(change.allocation, quota=True)  # resolves the change request

    assert queued == [("ticketing.tasks.resolve_allocation_change_ticket", (change.pk,), {})]
    assert markers(change.allocation) == [f"coldfront:allocation:{change.allocation.pk}"]


def test_a_marker_that_cant_be_saved_is_logged_and_the_resolve_goes_ahead(pi, admin, in_request, caplog):
    allocation, _ = make_storage_allocation(pi, path=None)  # no "Text" attribute type to build the marker from
    in_request(admin, off=True)

    allocation_activate.send(sender=AllocationDetailView, allocation_pk=allocation.pk)

    assert markers(allocation) == []
    assert "Could not record that ticketing is off" in caplog.text


# -- Project users: one notice per project, per day -------------------------------


@pytest.fixture
def lab(pi):
    return f.ProjectFactory(pi=pi, title="Genomics Lab")


def add(project, *usernames):
    """Add users the way ProjectAddUsersView does, through core's Project.add_user."""
    role = f.ProjectUserRoleChoiceFactory(name="User")
    f.ProjectUserStatusChoiceFactory(name="Active")
    for username in usernames:
        project.add_user(f.UserFactory(username=username), role, signal_sender=object)


def remove(project, *usernames):
    f.ProjectUserStatusChoiceFactory(name="Removed")
    for username in usernames:
        project.remove_user(project.projectuser_set.get(user__username=username).user, signal_sender=object)


def test_adding_several_users_in_one_request_queues_one_notice(lab, requestor, in_request, queued, committed):
    in_request(requestor)

    with committed():
        add(lab, "u1", "u2", "u3")

    assert queued == [("ticketing.tasks.create_project_users_ticket", (lab.pk,), {"requestor_pk": requestor.pk})]


def test_adds_and_removes_in_one_request_queue_one_notice(lab, requestor, in_request, queued, committed):
    add(lab, "u1", "u2")
    in_request(requestor)

    with committed():
        add(lab, "u3")
        remove(lab, "u1", "u2")

    assert queued == [("ticketing.tasks.create_project_users_ticket", (lab.pk,), {"requestor_pk": requestor.pk})]


def test_removing_users_queues_the_same_notice(lab, requestor, in_request, queued, committed):
    add(lab, "u1")
    in_request(requestor)

    with committed():
        remove(lab, "u1")

    assert queued == [("ticketing.tasks.create_project_users_ticket", (lab.pk,), {"requestor_pk": requestor.pk})]


def test_each_project_gets_its_own_notice(lab, pi, requestor, in_request, queued, committed):
    other = f.ProjectFactory(pi=pi, title="Other Lab")
    in_request(requestor)

    with committed():
        add(lab, "u1")
        add(other, "u2")

    assert [args for _, args, _ in queued] == [(lab.pk,), (other.pk,)]


def test_outside_a_request_each_add_queues_and_the_task_dedupes(lab, queued, committed):
    with committed():
        add(lab, "u1", "u2")

    assert [args for _, args, _ in queued] == [(lab.pk,), (lab.pk,)]
    assert queued[0][2] == {"requestor_pk": None}


def test_creating_a_project_queues_no_notice_for_its_pi(lab, pi, in_request, queued, committed):
    """Tufts' project-create view adds the PI through Project.add_user, sending project_activate_user."""
    in_request(pi)
    role = f.ProjectUserRoleChoiceFactory(name="Manager")
    f.ProjectUserStatusChoiceFactory(name="Active")

    with committed():
        lab.add_user(pi, role, signal_sender=object)

    assert queued == []


def test_the_pis_add_doesnt_use_up_the_request(lab, pi, in_request, queued, committed):
    in_request(pi)
    role = f.ProjectUserRoleChoiceFactory(name="Manager")
    f.ProjectUserStatusChoiceFactory(name="Active")

    with committed():
        lab.add_user(pi, role, signal_sender=object)
        add(lab, "u1")

    assert queued == [("ticketing.tasks.create_project_users_ticket", (lab.pk,), {"requestor_pk": pi.pk})]


@pytest.mark.parametrize("who", ["admin", "ticketing_admin"])
def test_ticketing_admins_added_queue_nothing_and_dont_use_up_the_request(
    lab, requestor, in_request, queued, committed, request, who
):
    added = request.getfixturevalue(who)
    in_request(requestor)
    role = f.ProjectUserRoleChoiceFactory(name="User")
    f.ProjectUserStatusChoiceFactory(name="Active")

    with committed():
        lab.add_user(added, role, signal_sender=object)
        add(lab, "u1")

    assert queued == [("ticketing.tasks.create_project_users_ticket", (lab.pk,), {"requestor_pk": requestor.pk})]


def test_admin_with_ticketing_off_queues_no_project_notice(lab, admin, in_request, queued, committed):
    in_request(admin, off=True)

    with committed():
        add(lab, "u1")

    assert queued == []


# -- HPC accounts ---------------------------------------------------------------------------------


def test_an_account_awaiting_a_uid_opens_its_ticket(queued, committed):
    with committed():
        hpc_account_requested.send(sender=None, username="jdoe01")

    assert queued == [("ticketing.tasks.create_hpc_account_ticket", ("jdoe01",), {})]


def test_a_provisioned_account_is_welcomed(queued, committed):
    with committed():
        hpc_account_provisioned.send(
            sender=None, username="jdoe01", home_path="/cluster/home/jdoe01", scratch_path="/cluster/scratch/jdoe01"
        )

    assert queued == [("ticketing.tasks.resolve_hpc_account_ticket", ("jdoe01", "/cluster/home/jdoe01"), {})]


def test_a_failed_account_setup_files_a_failure_ticket(queued, committed):
    with committed():
        hpc_account_provisioning_failed.send(sender=None, username="jdoe01", message="pyinfra failed", exception=None)

    assert queued == [("ticketing.tasks.file_hpc_account_failure_ticket", ("jdoe01", "pyinfra failed"), {})]


def test_a_provisioned_account_also_resolves_an_open_failure_ticket(requestor, queued, committed):
    FiledTicket.objects.create(
        correlation_id="coldfront:hpc-account:jdoe01:failed",
        kind=FiledTicket.Kind.HPC_ACCOUNT_FAILURE,
        number="INC0000003",
        sys_id="i-3",
        user=requestor,
    )

    with committed():
        hpc_account_provisioned.send(
            sender=None, username="jdoe01", home_path="/cluster/home/jdoe01", scratch_path="/cluster/scratch/jdoe01"
        )

    assert queued == [
        ("ticketing.tasks.resolve_hpc_account_failure_ticket", ("jdoe01",), {}),
        ("ticketing.tasks.resolve_hpc_account_ticket", ("jdoe01", "/cluster/home/jdoe01"), {}),
    ]
