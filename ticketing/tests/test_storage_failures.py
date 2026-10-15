import pytest
from coldfront.core.test_helpers import factories as f

from ticketing import tasks
from ticketing.client import IncidentRef
from ticketing.models import FiledTicket
from ticketing.tests.test_allocation_updates import (
    FakeServiceNow,
    NoSuchUser,
    make_storage_change,
    set_status,
    submitted_by,
)
from ticketing.tickets import ServiceNowIncident

pytestmark = pytest.mark.django_db


@pytest.fixture
def snow(monkeypatch):
    client = FakeServiceNow()
    monkeypatch.setattr(tasks, "client_from_settings", lambda: client)
    return client


def activated(in_request, requestor, admin, pi):
    """A storage allocation `requestor` asked for and `admin` activated, as the views record it."""
    allocation = submitted_by(in_request, requestor, pi)
    set_status(in_request, admin, allocation, f.AllocationStatusChoiceFactory(name="Active"))
    return allocation


def open_request_ticket(snow, correlation_id):
    return snow.create_incident(
        ServiceNowIncident(
            u_contact_person="jdoe01", caller_id="jdoe01", short_description="x", correlation_id=correlation_id
        )
    )


def failure_of(snow, correlation_id):
    return snow.incidents[f"{correlation_id}:storage-failed"]


def test_a_failed_share_files_an_admin_only_child_of_the_request(pi, requestor, admin, in_request, snow):
    allocation = activated(in_request, requestor, admin, pi)
    request = open_request_ticket(snow, f"coldfront:allocation:{allocation.pk}")

    incident = tasks.file_storage_failure_ticket(allocation.pk, state="failed: missing information")

    _, ticket = failure_of(snow, f"coldfront:allocation:{allocation.pk}")
    assert ticket["parent"] == request.number
    assert ticket["caller_id"] == ticket["u_contact_person"] == "admin01"
    assert ticket["watch_list"] is None
    assert ticket["assigned_to"] is None  # waits in the group queue
    assert ticket["comments"] is None  # nothing customer-visible
    assert ticket["contact_type"] == "monitoring"
    assert ticket["u_action"] == "Activate/Setup/Install"  # as for the request: a new share
    assert ticket["short_description"].startswith("Research Storage: Storage Job Failed | ")
    assert "State:        failed: missing information" in ticket["description"]
    assert f"Request:      {request.number}" in ticket["description"]
    assert "Share creation" in ticket["description"]
    record = FiledTicket.objects.get(correlation_id=f"coldfront:allocation:{allocation.pk}:storage-failed")
    assert (record.kind, record.number, record.allocation) == (
        FiledTicket.Kind.STORAGE_FAILURE,
        incident.number,
        allocation,
    )
    assert record.resolved_at is None


def test_without_a_request_ticket_it_has_no_parent(pi, admin, in_request, snow):
    allocation = activated(in_request, admin, admin, pi)  # an admin's request, not linked

    tasks.file_storage_failure_ticket(allocation.pk)

    _, ticket = failure_of(snow, f"coldfront:allocation:{allocation.pk}")
    assert ticket["parent"] is None
    assert "no ticket: submitted by a ticketing admin" in ticket["description"]


def test_a_failed_quota_change_names_the_change_requests_approver(
    pi, requestor, admin, in_request, snow, django_user_model
):
    change = make_storage_change(pi)
    other = django_user_model.objects.create_superuser(username="admin02", password="pw")
    set_status(in_request, other, change, f.AllocationChangeStatusChoiceFactory(name="Approved"))
    request = open_request_ticket(snow, f"coldfront:allocation-change:{change.pk}")

    tasks.file_storage_failure_ticket(change.allocation.pk, change.pk, state="failed")

    _, ticket = failure_of(snow, f"coldfront:allocation-change:{change.pk}")
    assert ticket["parent"] == request.number
    assert ticket["caller_id"] == "admin02"
    assert ticket["u_action"] == "Move/Add/Change"
    assert "Quota change" in ticket["description"]
    assert FiledTicket.objects.get(kind=FiledTicket.Kind.STORAGE_FAILURE).allocation_change == change


def test_an_unrecorded_approver_falls_back_to_the_service_account(pi, snow):
    allocation = f.AllocationFactory(project=f.ProjectFactory(pi=pi))  # saved outside a request

    tasks.file_storage_failure_ticket(allocation.pk)

    _, ticket = failure_of(snow, f"coldfront:allocation:{allocation.pk}")
    assert ticket["caller_id"] == ticket["u_contact_person"] == "svc_test"


def test_an_approver_servicenow_doesnt_know_falls_back_to_the_service_account(
    pi, requestor, admin, in_request, monkeypatch, caplog
):
    snow = NoSuchUser("admin01")
    monkeypatch.setattr(tasks, "client_from_settings", lambda: snow)
    allocation = activated(in_request, requestor, admin, pi)

    tasks.file_storage_failure_ticket(allocation.pk)

    _, ticket = failure_of(snow, f"coldfront:allocation:{allocation.pk}")
    assert ticket["caller_id"] == "svc_test"
    assert "ServiceNow has no user admin01" in caplog.text


def test_a_repeat_failure_adds_a_work_note_instead_of_another_ticket(pi, requestor, admin, in_request, snow):
    allocation = activated(in_request, requestor, admin, pi)
    first = tasks.file_storage_failure_ticket(allocation.pk, state="failed")

    again = tasks.file_storage_failure_ticket(allocation.pk, state="failed: missing information")

    assert again == first
    assert len(snow.incidents) == 1
    _, fields = failure_of(snow, f"coldfront:allocation:{allocation.pk}")
    assert fields["work_notes"] == "Share creation failed again: failed: missing information"


def test_a_failure_after_its_ticket_was_resolved_reopens_it(pi, requestor, admin, in_request, snow):
    allocation = activated(in_request, requestor, admin, pi)
    tasks.file_storage_failure_ticket(allocation.pk)
    tasks.resolve_storage_failure_ticket(allocation.pk)

    tasks.file_storage_failure_ticket(allocation.pk)

    incident, _ = failure_of(snow, f"coldfront:allocation:{allocation.pk}")
    assert incident.state == "2"
    assert FiledTicket.objects.get(kind=FiledTicket.Kind.STORAGE_FAILURE).resolved_at is None


def test_a_closed_failure_ticket_is_left_alone(pi, requestor, admin, in_request, snow, caplog):
    allocation = activated(in_request, requestor, admin, pi)
    filed = tasks.file_storage_failure_ticket(allocation.pk)
    cid = f"coldfront:allocation:{allocation.pk}:storage-failed"
    snow.incidents[cid] = (IncidentRef(filed.sys_id, filed.number, "7"), snow.incidents[cid][1])

    tasks.file_storage_failure_ticket(allocation.pk)

    assert "work_notes" not in snow.incidents[cid][1] or snow.incidents[cid][1]["work_notes"] is None
    assert "is closed" in caplog.text


def test_success_resolves_the_failure_ticket(pi, requestor, admin, in_request, snow):
    allocation = activated(in_request, requestor, admin, pi)
    tasks.file_storage_failure_ticket(allocation.pk)

    tasks.resolve_storage_failure_ticket(allocation.pk)

    incident, fields = failure_of(snow, f"coldfront:allocation:{allocation.pk}")
    assert incident.state == "6"
    assert fields["assigned_to"] == "svc_test"
    assert fields["close_notes"] == "Share creation succeeded on a later attempt."
    assert FiledTicket.objects.get(kind=FiledTicket.Kind.STORAGE_FAILURE).resolved_at is not None


def test_resolving_with_no_failure_ticket_does_nothing(pi, requestor, admin, in_request, snow):
    allocation = activated(in_request, requestor, admin, pi)

    assert tasks.resolve_storage_failure_ticket(allocation.pk) is None
    assert snow.incidents == {}
