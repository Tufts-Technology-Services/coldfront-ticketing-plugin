import datetime

import pytest
from django.contrib.auth import get_user_model

from ticketing import tasks
from ticketing.client import Incident
from ticketing.tests.test_allocation_updates import FakeServiceNow


class FakeClient:
    """Records what a task would send to ServiceNow."""

    def __init__(self, existing=None):
        self.existing = existing
        self.created = []
        self.updated = []
        self.looked_up = []

    def find_incident(self, correlation_id):
        self.looked_up.append(correlation_id)
        return self.existing

    def create_incident(self, ticket):
        self.created.append(ticket)
        return Incident(sys_id="i-1", number="INC0000001")

    def update_incident(self, sys_id, fields):
        self.updated.append((sys_id, fields))


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(tasks, "client_from_settings", lambda: client)
    return client


@pytest.fixture
def use_client(monkeypatch):
    def install(client):
        monkeypatch.setattr(tasks, "client_from_settings", lambda: client)
        return client

    return install


pytestmark = pytest.mark.django_db


# -- HPC accounts --------------------------------------------------------------------------------


def test_account_waiting_on_a_uid_opens_an_incident_for_admins(requestor, fake_client):
    incident = tasks.create_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    assert incident == Incident(sys_id="i-1", number="INC0000001")
    [ticket] = fake_client.created
    assert ticket.caller_id == ticket.u_contact_person == "jdoe01"
    assert ticket.contact_type == "self-service"
    assert ticket.state is None and ticket.assigned_to is None  # waits New in the group queue
    assert ticket.short_description == "HPC Account Creation | /cluster/home/jdoe01"
    assert ticket.correlation_id == "coldfront:hpc-account:jdoe01"
    assert ticket.comments is None  # nothing customer-facing until the account exists
    assert "waiting on a UID" in ticket.description
    assert "jdoe01" in ticket.description and "/cluster/home/jdoe01" in ticket.description


def test_account_waiting_on_a_uid_rerun_returns_the_existing_incident(requestor, use_client):
    existing = Incident(sys_id="i-0", number="INC0000000", state="1")
    client = use_client(FakeClient(existing=existing))

    assert tasks.create_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01") == existing
    assert client.looked_up == ["coldfront:hpc-account:jdoe01"]
    assert client.created == []


def test_account_created_resolves_the_waiting_incident_with_the_welcome(requestor, use_client):
    client = use_client(FakeClient(existing=Incident(sys_id="i-0", number="INC0000000", state="1")))

    assert tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01") == Incident("i-0", "INC0000000")

    assert client.looked_up == ["coldfront:hpc-account:jdoe01"]
    [(sys_id, fields)] = client.updated
    assert sys_id == "i-0"
    assert fields["state"] == "6"
    assert fields["close_code"] == "Solved Remotely (Permanently)"
    assert fields["close_notes"] == "Cluster account created!"
    assert fields["assigned_to"] == "svc_test"  # no one had picked it up
    assert fields["comments"].startswith("Hello,")
    assert client.created == []


def test_account_created_without_a_waiting_incident_files_a_resolved_one(requestor, fake_client):
    incident = tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    assert incident == Incident(sys_id="i-1", number="INC0000001")
    [ticket] = fake_client.created
    assert ticket.caller_id == ticket.u_contact_person == "jdoe01"
    assert ticket.assigned_to == "svc_test"  # TICKETING_ASSIGNED_TO in tests/settings.py
    assert ticket.contact_type == "self-service"
    assert ticket.short_description == "HPC Account Creation | /cluster/home/jdoe01"
    assert ticket.u_business_service == "High Performance Computing (HPC)"
    assert ticket.u_action == "Activate/Setup/Install"
    assert ticket.state == "6"
    assert ticket.close_code == "Solved Remotely (Permanently)"
    assert ticket.close_notes == "Cluster account created!"
    assert ticket.correlation_id == "coldfront:hpc-account:jdoe01"


def test_welcome_text_goes_in_the_customer_visible_comments(requestor, fake_client):
    tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    comments = fake_client.created[0].comments
    assert comments.startswith("Hello,")
    assert "Tufts HPC cluster" in comments  # from partials/hpc_welcome.txt
    assert "Research Technology (RT)" in comments  # from partials/footer.txt
    assert "&amp;" not in comments and "{%" not in comments


def test_waiting_then_created_resolves_the_same_incident(requestor, use_client):
    snow = use_client(FakeServiceNow())

    opened = tasks.create_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")
    resolved = tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")
    tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")  # re-run: already resolved

    assert resolved == opened
    [(incident, fields)] = snow.incidents.values()
    assert incident.state == "6"
    assert fields["assigned_to"] == "svc_test"
    assert fields["comments"].count("Hello,") == 1
    assert "waiting on a UID" in fields["description"]  # the admin summary is kept


def test_account_already_resolved_is_left_alone(requestor, use_client):
    client = use_client(FakeClient(existing=Incident(sys_id="i-0", number="INC0000000", state="6")))

    tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    assert client.updated == [] and client.created == []


@pytest.mark.parametrize("task", [tasks.create_hpc_account_ticket, tasks.resolve_hpc_account_ticket])
def test_unknown_user_raises_before_calling_servicenow(fake_client, task):
    with pytest.raises(get_user_model().DoesNotExist):
        task("nobody", "/cluster/home/nobody")

    assert fake_client.looked_up == [] and fake_client.created == []


@pytest.fixture(autouse=True)
def today(monkeypatch):
    """Pin the date the group tasks put in their correlation_id."""
    day = {"today": datetime.date(2026, 9, 28)}
    monkeypatch.setattr(tasks.timezone, "localdate", lambda: day["today"])
    return day


def add_to_group(requestor_username=None):
    return tasks.create_group_membership_ticket(
        "jdoe01", "lab_smith", "/cluster/tufts/smith_lab", "/cluster/tufts/smith_lab/jdoe01", requestor_username
    )


def test_group_membership_files_a_resolved_incident_for_the_added_user(requestor, pi, fake_client):
    assert add_to_group("asmith02") == Incident(sys_id="i-1", number="INC0000001")

    [ticket] = fake_client.created
    assert ticket.caller_id == "jdoe01"
    assert ticket.u_contact_person == "asmith02"
    assert ticket.watch_list == ["asmith02"]
    assert ticket.contact_type == "self-service"
    assert ticket.short_description == "Tier 1 HPC Storage: Grant Access to /cluster/tufts/smith_lab/jdoe01"
    assert ticket.u_business_service == "Research Data Storage"
    assert ticket.u_action == "Move/Add/Change"
    assert ticket.assigned_to == "svc_test"
    assert ticket.state == "6"
    assert ticket.close_code == "Solved Remotely (Permanently)"
    assert ticket.close_notes == "Added user to group!"
    assert ticket.correlation_id == "coldfront:group-add:lab_smith:jdoe01:2026-09-28"


def test_group_membership_notice_goes_in_the_customer_visible_comments(requestor, fake_client):
    add_to_group()

    comments = fake_client.created[0].comments
    assert "You now have access to group directory /cluster/tufts/smith_lab" in comments
    assert "(i.e., /cluster/tufts/smith_lab/jdoe01)" in comments
    assert "getent group lab_smith" in comments
    assert "Research Technology (RT)" in comments  # from partials/footer.txt
    assert "&amp;" not in comments and "{%" not in comments


def test_group_membership_without_a_requestor_is_the_added_user_alone(requestor, fake_client):
    add_to_group()

    [ticket] = fake_client.created
    assert ticket.caller_id == ticket.u_contact_person == "jdoe01"
    assert ticket.watch_list is None
    assert ticket.contact_type == "self-service"


def test_group_membership_added_by_a_superuser_is_on_their_behalf(requestor, django_user_model, fake_client):
    django_user_model.objects.create_superuser(username="admin01", email="admin01@example.edu", password="pw")

    add_to_group("admin01")

    [ticket] = fake_client.created
    assert ticket.u_contact_person == "admin01"
    assert ticket.contact_type == "email"


def test_group_membership_files_nothing_for_a_superuser(django_user_model, fake_client):
    django_user_model.objects.create_superuser(username="jdoe01", email="jdoe01@example.edu", password="pw")

    assert add_to_group() is None
    assert fake_client.looked_up == [] and fake_client.created == []


def remove_from_group(requestor_username=None):
    return tasks.create_group_removal_ticket("jdoe01", "lab_smith", "/cluster/tufts/smith_lab", requestor_username)


def test_group_removal_files_a_resolved_incident_for_the_removed_user(requestor, pi, fake_client):
    assert remove_from_group("asmith02") == Incident(sys_id="i-1", number="INC0000001")

    [ticket] = fake_client.created
    assert ticket.caller_id == "jdoe01"
    assert ticket.u_contact_person == "asmith02"
    assert ticket.watch_list == ["asmith02"]
    assert ticket.contact_type == "self-service"
    assert ticket.short_description == "Tier 1 HPC Storage: Remove Access to /cluster/tufts/smith_lab"
    assert ticket.u_business_service == "Research Data Storage"
    assert ticket.u_action == "Move/Add/Change"
    assert ticket.state == "6"
    assert ticket.close_code == "Solved Remotely (Permanently)"
    assert ticket.close_notes == "Removed user from group!"
    assert ticket.correlation_id == "coldfront:group-remove:lab_smith:jdoe01:2026-09-28"


def test_group_removal_notice_goes_in_the_customer_visible_comments(requestor, fake_client):
    remove_from_group()

    comments = fake_client.created[0].comments
    assert "removed from group lab_smith" in comments
    assert "no longer have access to group directory /cluster/tufts/smith_lab" in comments
    assert "Research Technology (RT)" in comments
    assert "&amp;" not in comments and "{%" not in comments


def test_group_removal_files_nothing_for_a_superuser(django_user_model, fake_client):
    django_user_model.objects.create_superuser(username="jdoe01", email="jdoe01@example.edu", password="pw")

    assert remove_from_group() is None
    assert fake_client.looked_up == [] and fake_client.created == []


def test_group_membership_rerun_returns_the_existing_incident(requestor, monkeypatch):
    existing = Incident(sys_id="i-0", number="INC0000000")
    client = FakeClient(existing=existing)
    monkeypatch.setattr(tasks, "client_from_settings", lambda: client)

    assert add_to_group() == existing
    assert client.looked_up == ["coldfront:group-add:lab_smith:jdoe01:2026-09-28"]
    assert client.created == []


def test_group_membership_on_a_later_day_files_a_new_incident(requestor, today, monkeypatch):
    client = FakeClient(existing=Incident(sys_id="i-0", number="INC0000000"))
    monkeypatch.setattr(tasks, "client_from_settings", lambda: client)
    add_to_group()

    today["today"] = datetime.date(2026, 10, 5)
    client.existing = None  # nothing filed under the new day's correlation_id
    assert add_to_group() == Incident(sys_id="i-1", number="INC0000001")

    assert client.looked_up == [
        "coldfront:group-add:lab_smith:jdoe01:2026-09-28",
        "coldfront:group-add:lab_smith:jdoe01:2026-10-05",
    ]
    [ticket] = client.created
    assert ticket.correlation_id == "coldfront:group-add:lab_smith:jdoe01:2026-10-05"
