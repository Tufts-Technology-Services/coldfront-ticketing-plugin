import pytest

from ticketing import client as servicenow
from ticketing import tasks
from ticketing.client import IncidentRef
from ticketing.models import FiledTicket
from ticketing.tests.test_allocation_updates import FakeServiceNow

pytestmark = pytest.mark.django_db

FAILURE = "coldfront:hpc-account:jdoe01:failed"


@pytest.fixture
def snow(monkeypatch):
    client = FakeServiceNow()
    monkeypatch.setattr(servicenow, "client_from_settings", lambda: client)
    return client


def test_a_failure_files_an_admin_only_child_of_the_account_ticket(requestor, snow):
    request = tasks.create_hpc_account_ticket("jdoe01")

    incident = tasks.file_hpc_account_failure_ticket("jdoe01", "pyinfra could not reach the filesystem host")

    _, ticket = snow.incidents[FAILURE]
    assert ticket["parent"] == request.number
    assert ticket["caller_id"] == ticket["u_contact_person"] == "svc_test"  # a signup has no approver
    assert ticket["watch_list"] is None
    assert ticket["assigned_to"] is None  # waits in the group queue
    assert ticket["comments"] is None  # nothing customer-visible
    assert ticket["contact_type"] == "monitoring"
    assert ticket["short_description"] == "HPC Account Setup Failed | jdoe01"
    assert "Reason:   pyinfra could not reach the filesystem host" in ticket["description"]
    assert f"Request:  {request.number}" in ticket["description"]
    assert 'provision_hpc_account("jdoe01")' in ticket["description"]
    record = FiledTicket.objects.get(correlation_id=FAILURE)
    assert (record.kind, record.number, record.user) == (
        FiledTicket.Kind.HPC_ACCOUNT_FAILURE,
        incident.number,
        requestor,
    )
    assert record.resolved_at is None


def test_without_an_account_ticket_it_has_no_parent(requestor, snow):
    """The usual case: the account didn't have to wait for a UID, so no ticket was opened for it."""
    tasks.file_hpc_account_failure_ticket("jdoe01", "could not add them to the access group")

    _, ticket = snow.incidents[FAILURE]
    assert ticket["parent"] is None
    assert "no ticket: the account didn't have to wait for a UID" in ticket["description"]


def test_a_repeat_failure_adds_a_work_note_instead_of_another_ticket(requestor, snow):
    first = tasks.file_hpc_account_failure_ticket("jdoe01", "first")

    again = tasks.file_hpc_account_failure_ticket("jdoe01", "second")

    assert again == first
    assert len(snow.incidents) == 1
    _, fields = snow.incidents[FAILURE]
    assert fields["work_notes"] == "Setting up the HPC account failed again: second"


def test_a_failure_after_its_ticket_was_resolved_reopens_it(requestor, snow):
    tasks.file_hpc_account_failure_ticket("jdoe01", "first")
    tasks.resolve_hpc_account_failure_ticket("jdoe01")

    tasks.file_hpc_account_failure_ticket("jdoe01", "again")

    incident, _ = snow.incidents[FAILURE]
    assert incident.state == "2"
    assert FiledTicket.objects.get(kind=FiledTicket.Kind.HPC_ACCOUNT_FAILURE).resolved_at is None


def test_a_closed_failure_ticket_is_left_alone(requestor, snow, caplog):
    filed = tasks.file_hpc_account_failure_ticket("jdoe01", "first")
    snow.incidents[FAILURE] = (IncidentRef(filed.sys_id, filed.number, "7"), snow.incidents[FAILURE][1])

    tasks.file_hpc_account_failure_ticket("jdoe01", "again")

    assert snow.incidents[FAILURE][1].get("work_notes") is None
    assert "is closed" in caplog.text


def test_success_resolves_the_failure_ticket(requestor, snow):
    tasks.file_hpc_account_failure_ticket("jdoe01", "first")

    tasks.resolve_hpc_account_failure_ticket("jdoe01")

    incident, fields = snow.incidents[FAILURE]
    assert incident.state == "6"
    assert fields["assigned_to"] == "svc_test"
    assert fields["close_notes"] == "Setting up the HPC account succeeded on a later attempt."
    assert FiledTicket.objects.get(kind=FiledTicket.Kind.HPC_ACCOUNT_FAILURE).resolved_at is not None


def test_resolving_with_no_failure_ticket_does_nothing(requestor, snow):
    assert tasks.resolve_hpc_account_failure_ticket("jdoe01") is None
    assert snow.incidents == {}
