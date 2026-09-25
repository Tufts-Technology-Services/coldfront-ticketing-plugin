import pytest
from django.contrib.auth import get_user_model

from ticketing import tasks
from ticketing.client import Incident


class FakeClient:
    """Records what a task would send to ServiceNow."""

    def __init__(self, existing=None):
        self.existing = existing
        self.created = []
        self.looked_up = []

    def find_incident(self, correlation_id):
        self.looked_up.append(correlation_id)
        return self.existing

    def create_incident(self, ticket):
        self.created.append(ticket)
        return Incident(sys_id="i-1", number="INC0000001")


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(tasks, "client_from_settings", lambda: client)
    return client


pytestmark = pytest.mark.django_db


def test_files_a_resolved_incident_for_the_new_user(requestor, fake_client):
    incident = tasks.create_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    assert incident == Incident(sys_id="i-1", number="INC0000001")
    [ticket] = fake_client.created
    assert ticket.caller_id == ticket.u_contact_person == "jdoe01"
    assert ticket.assigned_to == "svc_test"  # TICKETING_ASSIGNED_TO in tests/settings.py
    assert ticket.contact_type == "self-service"
    assert ticket.short_description == "HPC Account Creation | /cluster/home/jdoe01"
    assert ticket.state == "6"
    assert ticket.close_code == "Solved Remotely (Permanently)"
    assert ticket.close_notes == "Cluster account created!"
    assert ticket.correlation_id == "coldfront:hpc-account:jdoe01"


def test_welcome_text_goes_in_the_customer_visible_comments(requestor, fake_client):
    tasks.create_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    comments = fake_client.created[0].comments
    assert comments.startswith("Hello,")
    assert "Tufts HPC cluster" in comments  # from partials/hpc_welcome.txt
    assert "Research Technology (RT)" in comments  # from partials/footer.txt
    assert "&amp;" not in comments and "{%" not in comments


def test_rerun_returns_the_existing_incident_without_filing_another(requestor, monkeypatch):
    existing = Incident(sys_id="i-0", number="INC0000000")
    client = FakeClient(existing=existing)
    monkeypatch.setattr(tasks, "client_from_settings", lambda: client)

    assert tasks.create_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01") == existing
    assert client.looked_up == ["coldfront:hpc-account:jdoe01"]
    assert client.created == []


def test_unknown_user_raises_before_calling_servicenow(fake_client):
    with pytest.raises(get_user_model().DoesNotExist):
        tasks.create_hpc_account_ticket("nobody", "/cluster/home/nobody")

    assert fake_client.looked_up == [] and fake_client.created == []
