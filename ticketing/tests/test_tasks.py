import datetime

import pytest
from coldfront.core.test_helpers import factories as f
from django.contrib.auth import get_user_model
from django.utils import timezone

from ticketing import client as servicenow
from ticketing import slots, tasks
from ticketing.client import IncidentRef
from ticketing.models import FiledTicket
from ticketing.tests.rendering import capture_templates
from ticketing.tests.test_allocation_updates import (
    FakeServiceNow,
    make_storage_allocation,
    make_storage_change,
    partial_template,
)


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
        return IncidentRef(sys_id="i-1", number="INC0000001")

    def update_incident(self, sys_id, fields):
        self.updated.append((sys_id, fields))


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(servicenow, "client_from_settings", lambda: client)
    return client


@pytest.fixture
def use_client(monkeypatch):
    def install(client):
        monkeypatch.setattr(servicenow, "client_from_settings", lambda: client)
        return client

    return install


pytestmark = pytest.mark.django_db


# -- HPC accounts --------------------------------------------------------------------------------


def test_a_requested_account_opens_an_incident_telling_the_user(requestor, fake_client):
    with capture_templates() as rendered:
        incident = tasks.create_hpc_account_ticket("jdoe01")

    assert incident == IncidentRef(sys_id="i-1", number="INC0000001")
    [ticket] = fake_client.created
    assert ticket.caller_id == ticket.u_contact_person == "jdoe01"
    assert ticket.contact_type == "self-service"
    assert ticket.state is None and ticket.assigned_to is None  # waits New in the group queue
    assert ticket.short_description == "HPC Account Request"
    assert ticket.correlation_id == "coldfront:hpc-account:jdoe01"
    # a customer-visible comment, which ServiceNow emails to the contact person; the description matches
    assert rendered.main == "ticketing/hpc_account_request.txt"
    assert ticket.comments
    assert ticket.description == ticket.comments


def test_account_waiting_on_a_uid_rerun_returns_the_existing_incident(requestor, use_client):
    existing = IncidentRef(sys_id="i-0", number="INC0000000", state="1")
    client = use_client(FakeClient(existing=existing))

    assert tasks.create_hpc_account_ticket("jdoe01") == existing
    assert client.looked_up == ["coldfront:hpc-account:jdoe01"]
    assert client.created == []


def test_account_created_resolves_the_waiting_incident_with_the_welcome(requestor, use_client):
    client = use_client(FakeClient(existing=IncidentRef(sys_id="i-0", number="INC0000000", state="1")))

    assert tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01") == IncidentRef("i-0", "INC0000000")

    assert client.looked_up == ["coldfront:hpc-account:jdoe01"]
    [(sys_id, fields)] = client.updated
    assert sys_id == "i-0"
    assert fields["state"] == "6"
    assert fields["close_code"] == "Solved Remotely (Permanently)"
    assert fields["close_notes"] == "Cluster account created!"
    assert fields["assigned_to"] == "svc_test"  # no one had picked it up
    assert fields["comments"]
    assert client.created == []


def test_account_created_without_a_waiting_incident_files_a_resolved_one(requestor, fake_client):
    incident = tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    assert incident == IncidentRef(sys_id="i-1", number="INC0000001")
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
    with capture_templates() as rendered:
        tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    assert rendered.main == "ticketing/hpc_account_creation_notification.txt"
    assert partial_template("hpc_welcome") in rendered.names
    assert fake_client.created[0].comments


def test_waiting_then_created_resolves_the_same_incident(requestor, use_client):
    snow = use_client(FakeServiceNow())

    opened = tasks.create_hpc_account_ticket("jdoe01")
    request_text = snow.incidents["coldfront:hpc-account:jdoe01"][1]["description"]
    resolved = tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")
    tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")  # re-run: already resolved

    assert resolved == opened
    [(incident, fields)] = snow.incidents.values()
    assert incident.state == "6"
    assert fields["assigned_to"] == "svc_test"
    assert fields["comments"] != request_text  # the welcome, sent once
    assert fields["description"] == request_text  # the request's description is kept


def test_account_already_resolved_is_left_alone(requestor, use_client):
    client = use_client(FakeClient(existing=IncidentRef(sys_id="i-0", number="INC0000000", state="6")))

    tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    assert client.updated == [] and client.created == []


@pytest.mark.parametrize(
    ("task", "args"),
    [(tasks.create_hpc_account_ticket, ()), (tasks.resolve_hpc_account_ticket, ("/cluster/home/nobody",))],
)
def test_unknown_user_raises_before_calling_servicenow(fake_client, task, args):
    with pytest.raises(get_user_model().DoesNotExist):
        task("nobody", *args)

    assert fake_client.looked_up == [] and fake_client.created == []


@pytest.fixture(autouse=True)
def today(monkeypatch):
    """Pin the date the project user tasks put in their correlation_id."""
    day = {"today": datetime.date(2026, 9, 28)}
    monkeypatch.setattr(timezone, "localdate", lambda: day["today"])
    return day


# -- Project users --------------------------------------------------------------------------------


@pytest.fixture
def project(pi):
    """A project whose PI is a Manager on it, with one more Approver (role Manager)."""
    project = f.ProjectFactory(pi=pi, title="Genomics Lab")
    manager = f.ProjectUserRoleChoiceFactory(name="Manager")
    f.ProjectUserFactory(project=project, user=pi, role=manager)
    f.ProjectUserFactory(project=project, user=f.UserFactory(username="bjones03"), role=manager)
    return project


def test_files_a_resolved_notice_for_the_project(project, requestor, fake_client):
    incident = tasks.create_project_users_ticket(project.pk, requestor_pk=requestor.pk)

    assert incident == IncidentRef(sys_id="i-1", number="INC0000001")
    [ticket] = fake_client.created
    assert ticket.caller_id == "asmith02"  # the PI is always the Affected Client
    assert ticket.u_contact_person == "jdoe01"
    assert ticket.watch_list == ["asmith02", "bjones03"]  # the PI and the Approver
    assert ticket.contact_type == "email"  # jdoe01 isn't the PI or an Approver here
    assert ticket.short_description == "Project Users Changed | Genomics Lab"
    assert ticket.u_business_service == "Research Data Storage"
    assert ticket.u_action == "Move/Add/Change"
    assert ticket.assigned_to == "svc_test"
    assert ticket.state == "6" and ticket.close_code == "Solved Remotely (Permanently)"
    assert ticket.close_notes == "Project users updated."
    assert ticket.correlation_id == f"coldfront:project-users:{project.pk}:2026-09-28"


def test_notice_is_generic_and_links_the_project(project, fake_client):
    with capture_templates() as rendered:
        tasks.create_project_users_ticket(project.pk)

    assert rendered.main == "ticketing/project_users_changed_notification.txt"
    assert rendered.main_context["url"].endswith(f"/project/{project.pk}/")
    comments = fake_client.created[0].comments
    assert "Genomics Lab" in comments
    assert f"/project/{project.pk}/" in comments


def test_change_by_the_pi_watches_the_approvers(project, pi, fake_client):
    tasks.create_project_users_ticket(project.pk, requestor_pk=pi.pk)

    [ticket] = fake_client.created
    assert ticket.caller_id == "asmith02"
    assert ticket.watch_list == ["bjones03"]
    assert ticket.contact_type == "self-service"


def test_without_a_requestor_the_pi_stands_in(project, fake_client):
    tasks.create_project_users_ticket(project.pk)

    assert fake_client.created[0].caller_id == "asmith02"


def test_another_change_the_same_day_files_nothing(project, use_client):
    existing = IncidentRef(sys_id="i-0", number="INC0000000", state="6")
    client = use_client(FakeClient(existing=existing))

    assert tasks.create_project_users_ticket(project.pk) == existing
    assert client.looked_up == [f"coldfront:project-users:{project.pk}:2026-09-28"]
    assert client.created == [] and client.updated == []


def test_a_change_on_a_later_day_files_a_new_notice(project, today, fake_client):
    tasks.create_project_users_ticket(project.pk)
    today["today"] = datetime.date(2026, 9, 29)
    tasks.create_project_users_ticket(project.pk)

    assert [ticket.correlation_id for ticket in fake_client.created] == [
        f"coldfront:project-users:{project.pk}:2026-09-28",
        f"coldfront:project-users:{project.pk}:2026-09-29",
    ]


# -- A user added to a project ------------------------------------------------------------------


@pytest.fixture
def added(project):
    """A user just added to `project`."""
    user = f.UserFactory(username="cnew04")
    f.ProjectUserFactory(project=project, user=user, role=f.ProjectUserRoleChoiceFactory(name="User"))
    return user


def test_tells_the_added_user_and_closes_the_ticket(project, added, requestor, fake_client):
    incident = tasks.create_project_user_added_ticket(project.pk, added.pk, requestor_pk=requestor.pk)

    assert incident == IncidentRef(sys_id="i-1", number="INC0000001")
    [ticket] = fake_client.created
    # ServiceNow emails the comment to the contact person; the PI is the Affected Client
    assert ticket.caller_id == "asmith02" and ticket.u_contact_person == "cnew04"
    assert ticket.watch_list is None  # the PI and Approvers get the project notice instead
    assert ticket.contact_type == "email"  # jdoe01 isn't the PI or an Approver here
    assert ticket.short_description == "Added to Project | Genomics Lab"
    assert ticket.u_business_service == "Research Data Storage"
    assert ticket.u_action == "Move/Add/Change"
    assert ticket.assigned_to == "svc_test"
    assert ticket.state == "6" and ticket.close_code == "Solved Remotely (Permanently)"
    assert ticket.close_notes == "User added to project."
    assert ticket.correlation_id == f"coldfront:project-user-added:{project.pk}:cnew04:2026-09-28"


def test_added_user_message_names_the_project(project, added, fake_client):
    with capture_templates() as rendered:
        tasks.create_project_user_added_ticket(project.pk, added.pk)

    assert rendered.main == "ticketing/project_user_added_notification.txt"
    assert partial_template("rcrm") in rendered.names
    assert "Genomics Lab" in fake_client.created[0].comments


def adder_named_in(project, added, requestor_pk):
    """Who the added user's message names as having added them, and the message."""
    with capture_templates() as rendered:
        tasks.create_project_user_added_ticket(project.pk, added.pk, requestor_pk=requestor_pk)
    return rendered.main_context["added_by"], rendered.main_context


def test_added_by_an_approver_names_them(project, added, fake_client):
    approver = project.projectuser_set.get(user__username="bjones03").user

    named, _ = adder_named_in(project, added, approver.pk)

    assert named == approver
    assert approver.get_full_name() in fake_client.created[0].comments


def test_added_by_the_owner_names_them(project, added, pi, fake_client):
    named, _ = adder_named_in(project, added, pi.pk)

    assert named == pi


def test_added_by_staff_names_no_one(project, added, admin, fake_client):
    """An administrator acting for the project isn't someone the new member knows."""
    named, _ = adder_named_in(project, added, admin.pk)

    assert named is None
    assert admin.username not in fake_client.created[0].comments


def test_added_by_someone_unknown_names_no_one(project, added, fake_client):
    """Outside a request (a sync, say) there's no one to name, and the PI isn't assumed."""
    named, _ = adder_named_in(project, added, None)

    assert named is None


def test_added_by_the_pi_is_self_service(project, added, pi, fake_client):
    tasks.create_project_user_added_ticket(project.pk, added.pk, requestor_pk=pi.pk)

    [ticket] = fake_client.created
    assert ticket.caller_id == "asmith02" and ticket.u_contact_person == "cnew04"
    assert ticket.contact_type == "self-service"


def test_added_user_without_a_requestor_is_self_service_for_the_pi(project, added, fake_client):
    tasks.create_project_user_added_ticket(project.pk, added.pk)

    [ticket] = fake_client.created
    assert ticket.u_contact_person == "cnew04" and ticket.contact_type == "self-service"


def test_added_user_is_recorded_about_the_project_and_the_user(project, added, fake_client):
    tasks.create_project_user_added_ticket(project.pk, added.pk)

    filed = FiledTicket.objects.get()
    assert filed.kind == FiledTicket.Kind.PROJECT_USER_ADDED
    assert filed.project == project and filed.user == added
    assert filed.resolved_at is not None


def test_a_rerun_the_same_day_sends_nothing(project, added, fake_client):
    tasks.create_project_user_added_ticket(project.pk, added.pk)
    fake_client.looked_up.clear()

    assert tasks.create_project_user_added_ticket(project.pk, added.pk) == IncidentRef(
        sys_id="i-1", number="INC0000001"
    )
    assert len(fake_client.created) == 1
    assert fake_client.looked_up == []  # answered from the local record


def test_added_user_already_told_in_servicenow_is_left_alone(project, added, use_client):
    existing = IncidentRef(sys_id="i-0", number="INC0000000", state="6")
    client = use_client(FakeClient(existing=existing))

    assert tasks.create_project_user_added_ticket(project.pk, added.pk) == existing
    assert client.created == [] and client.updated == []


def test_each_user_and_project_gets_its_own_ticket(project, added, pi, fake_client):
    other = f.ProjectFactory(pi=pi, title="Other Lab")
    second = f.UserFactory(username="dnew05")

    tasks.create_project_user_added_ticket(project.pk, added.pk)
    tasks.create_project_user_added_ticket(project.pk, second.pk)
    tasks.create_project_user_added_ticket(other.pk, added.pk)

    assert [ticket.correlation_id for ticket in fake_client.created] == [
        f"coldfront:project-user-added:{project.pk}:cnew04:2026-09-28",
        f"coldfront:project-user-added:{project.pk}:dnew05:2026-09-28",
        f"coldfront:project-user-added:{other.pk}:cnew04:2026-09-28",
    ]


# -- AllocationTicketSlot ------------------------------------------------------------------------


def test_an_allocation_ticket_slot_knows_its_allocation_request(pi):
    allocation, _ = make_storage_allocation(pi)

    ticket = slots.AllocationTicketSlot.load(allocation.pk)

    assert ticket.correlation_id == f"coldfront:allocation:{allocation.pk}"
    assert ticket.kind == "allocation"
    assert ticket.subjects == {"allocation": allocation}
    assert ticket.table == "incident"
    assert (ticket.approved_status, ticket.job) == ("Active", "Share creation")
    assert ticket.url.endswith(f"/allocation/{allocation.pk}/")


def test_an_allocation_ticket_slot_loads_the_change_request_when_given_one(pi):
    change = make_storage_change(pi)

    ticket = slots.AllocationTicketSlot.load(change.allocation.pk, change.pk)

    assert ticket == slots.AllocationTicketSlot.of_change(change)
    assert ticket.correlation_id == f"coldfront:allocation-change:{change.pk}"
    assert ticket.subjects == {"allocation_change": change, "allocation": change.allocation}
    assert (ticket.request, ticket.allocation) == (change, change.allocation)
    assert (ticket.approved_status, ticket.job) == ("Approved", "Quota change")
    assert ticket.table == "incident"


def test_a_storage_failure_slot_is_about_the_same_request(pi):
    change = make_storage_change(pi)

    failure = slots.AllocationTicketSlot.of_change(change).storage_failure()

    assert failure.correlation_id == f"coldfront:allocation-change:{change.pk}:storage-failed"
    assert failure.kind == "storage-failure"
    assert failure.subjects == {"allocation_change": change, "allocation": change.allocation}
    assert failure.what == f"the storage job for allocation change {change.pk}"
