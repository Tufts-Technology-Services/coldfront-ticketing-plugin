import pytest
from coldfront.core.test_helpers import factories as f
from django.contrib.auth import get_user_model
from django.urls import reverse

from ticketing import tasks
from ticketing.allocation_updates import UnsupportedUpdate, describe_change
from ticketing.client import Incident, UnknownReference

pytestmark = pytest.mark.django_db


def make_storage_allocation(pi, *, resource_name="Research Storage", quota="10", path="/cluster/tufts/lab"):
    """A Storage allocation with a quota (a Storage Quota (TB) attribute) and a path; returns (allocation, quota)."""
    resource = f.ResourceFactory(name=resource_name, resource_type=f.ResourceTypeFactory(name="Storage"))
    allocation = f.AllocationFactory(project=f.ProjectFactory(pi=pi))
    allocation.resources.add(resource)
    quota_attr = f.AllocationAttributeFactory(
        allocation=allocation,
        allocation_attribute_type=f.AllocationAttributeTypeFactory(name="Storage Quota (TB)"),
        value=quota,
    )
    if path:
        f.AllocationAttributeFactory(
            allocation=allocation,
            allocation_attribute_type=f.AllocationAttributeTypeFactory(
                name="Storage Path", attribute_type=f.AAttributeTypeFactory(name="Text")
            ),
            value=path,
        )
    return allocation, quota_attr


def make_storage_change(pi, *, resource_name="Research Storage", old="10", new="20", path="/cluster/tufts/lab"):
    """An approved Storage Quota (TB) change: the attribute already holds the new value."""
    allocation, quota = make_storage_allocation(pi, resource_name=resource_name, quota=old, path=path)
    change = f.AllocationChangeRequestFactory(allocation=allocation)
    f.AllocationAttributeChangeRequestFactory(
        allocation_change_request=change, allocation_attribute=quota, new_value=new
    )
    quota.value = new  # what approval does
    quota.save()
    return change


def make_cluster_allocation(pi):
    """An allocation on a resource type with no customer message (only Storage has one today)."""
    resource = f.ResourceFactory(name="Research Cluster", resource_type=f.ResourceTypeFactory(name="Cluster"))
    allocation = f.AllocationFactory(project=f.ProjectFactory(pi=pi))
    allocation.resources.add(resource)
    return allocation


class FakeClient:
    def __init__(self, existing=None):
        self.existing = existing
        self.created = []
        self.updated = []

    def find_incident(self, correlation_id):
        self.looked_up = correlation_id
        return self.existing

    def create_incident(self, ticket):
        self.created.append(ticket)
        return Incident(sys_id="i-1", number="INC0000001")

    def update_incident(self, sys_id, fields):
        self.updated.append((sys_id, fields))


@pytest.fixture
def use_client(monkeypatch):
    def install(client):
        monkeypatch.setattr(tasks, "client_from_settings", lambda: client)
        return client

    return install


# -- describe_change ---------------------------------------------------------------------------


def test_storage_quota_change_message(pi):
    update = describe_change(make_storage_change(pi))

    assert update.short_description == "Research Storage: Quota Change | 20 TB | /cluster/tufts/lab"
    assert "from 10 TB to 20 TB has been applied to Research Storage (/cluster/tufts/lab)" in update.comments
    assert "Research Technology (RT)" in update.comments  # footer included
    assert update.business_service == "Research Data Storage"


def test_resource_specific_template_wins_over_the_type_template(pi, settings, tmp_path):
    override = tmp_path / "ticketing" / "allocation_update" / "tier-1-rstore.txt"
    override.parent.mkdir(parents=True)
    override.write_text("RStore says {{ old_quota }} -> {{ new_quota }} on {{ share_name }}")
    settings.TEMPLATES = [{**settings.TEMPLATES[0], "DIRS": [tmp_path, *settings.TEMPLATES[0].get("DIRS", [])]}]

    update = describe_change(make_storage_change(pi, resource_name="Tier 1 RStore", path="rt-lab-share"))

    assert update.comments == "RStore says 10 TB -> 20 TB on rt-lab-share"


def test_change_without_a_quota_change_is_unsupported(pi):
    change = make_storage_change(pi)
    change.allocationattributechangerequest_set.all().delete()

    with pytest.raises(UnsupportedUpdate, match="no Storage Quota"):
        describe_change(change)


def test_unknown_resource_type_is_unsupported(pi):
    change = make_storage_change(pi)
    resource = change.allocation.resources.first()
    resource.resource_type = f.ResourceTypeFactory(name="Cluster")
    resource.save()

    with pytest.raises(UnsupportedUpdate, match="'Cluster'"):
        describe_change(change)


# -- resolve_allocation_change_ticket ----------------------------------------------------------


def test_resolves_the_open_incident_for_the_change(pi, use_client):
    change = make_storage_change(pi)
    client = use_client(FakeClient(existing=Incident(sys_id="i-0", number="INC0000000", state="2")))

    assert tasks.resolve_allocation_change_ticket(change.pk) == Incident(sys_id="i-0", number="INC0000000")
    assert client.looked_up == f"coldfront:allocation-change:{change.pk}"
    [(sys_id, fields)] = client.updated
    assert sys_id == "i-0"
    assert fields["state"] == "6"
    assert fields["close_code"] == "Solved Remotely (Permanently)"
    assert fields["close_notes"] == "Quota updated."
    assert fields["assigned_to"] == "svc_test"  # no one had picked it up
    assert "to 20 TB" in fields["comments"]
    assert client.created == []


def test_resolving_keeps_the_assignee_an_admin_set(pi, use_client):
    change = make_storage_change(pi)
    picked_up = Incident(sys_id="i-0", number="INC0000000", state="2", assigned_to="u-admin")
    client = use_client(FakeClient(existing=picked_up))

    tasks.resolve_allocation_change_ticket(change.pk)

    [(_, fields)] = client.updated
    assert "assigned_to" not in fields
    assert fields["state"] == "6"


def test_already_resolved_incident_is_not_commented_on_again(pi, use_client):
    change = make_storage_change(pi)
    client = use_client(FakeClient(existing=Incident(sys_id="i-0", number="INC0000000", state="6")))

    tasks.resolve_allocation_change_ticket(change.pk)

    assert client.updated == [] and client.created == []


def test_files_a_resolved_incident_when_none_exists(pi, requestor, use_client):
    change = make_storage_change(pi)
    client = use_client(FakeClient())

    tasks.resolve_allocation_change_ticket(change.pk, requestor_pk=requestor.pk)

    [ticket] = client.created
    assert ticket.caller_id == ticket.u_contact_person == "jdoe01"
    assert ticket.watch_list == ["asmith02"]  # the PI, since someone else asked
    assert ticket.contact_type == "email"  # jdoe01 isn't the PI or a Manager on the project
    assert ticket.short_description.startswith("Research Storage: Quota Change")
    assert ticket.u_business_service == "Research Data Storage"
    assert ticket.u_action == "Move/Add/Change"
    assert ticket.state == "6" and ticket.close_code == "Solved Remotely (Permanently)"
    assert ticket.assigned_to == "svc_test"
    assert ticket.correlation_id == f"coldfront:allocation-change:{change.pk}"


def test_without_a_requestor_the_pi_is_the_caller(pi, use_client):
    change = make_storage_change(pi)
    client = use_client(FakeClient())

    tasks.resolve_allocation_change_ticket(change.pk)

    [ticket] = client.created
    assert ticket.caller_id == "asmith02"
    assert ticket.watch_list is None
    assert ticket.contact_type == "self-service"


def test_unsupported_change_leaves_servicenow_alone(pi, use_client):
    change = make_storage_change(pi)
    change.allocationattributechangerequest_set.all().delete()
    client = use_client(FakeClient(existing=Incident(sys_id="i-0", number="INC0000000", state="2")))

    assert tasks.resolve_allocation_change_ticket(change.pk) is None
    assert client.updated == [] and client.created == []


# -- contact_type_for --------------------------------------------------------------------------


def test_contact_type_pi_and_manager_are_self_service(pi, requestor):
    project = f.ProjectFactory(pi=pi)
    f.ProjectUserFactory(project=project, user=requestor, role=f.ProjectUserRoleChoiceFactory(name="Manager"))

    assert tasks.contact_type_for(pi, project) == "self-service"
    assert tasks.contact_type_for(requestor, project) == "self-service"


def test_contact_type_superuser_on_someone_elses_project_is_email(pi, django_user_model):
    admin = django_user_model.objects.create_superuser(username="admin01", email="a@example.edu", password="pw")

    assert tasks.contact_type_for(admin, f.ProjectFactory(pi=pi)) == "email"


# -- project_watchers --------------------------------------------------------------------------


def add_member(project, username, role="Manager", status="Active", enable_notifications=True):
    """Put a user on the project. "Manager" is the role Tufts labels Approver."""
    user = f.UserFactory(username=username)
    f.ProjectUserFactory(
        project=project,
        user=user,
        role=f.ProjectUserRoleChoiceFactory(name=role),
        status=f.ProjectUserStatusChoiceFactory(name=status),
        enable_notifications=enable_notifications,
    )
    return user


@pytest.fixture
def project_with_approvers(pi):
    """A project whose PI is a Manager on it (as ColdFront makes them), with two Approvers."""
    project = f.ProjectFactory(pi=pi)
    f.ProjectUserFactory(project=project, user=pi, role=f.ProjectUserRoleChoiceFactory(name="Manager"))
    add_member(project, "czhang04")
    add_member(project, "bjones03")
    return project


def test_pi_request_watches_the_approvers(pi, project_with_approvers):
    assert tasks.project_watchers(project_with_approvers, pi) == ["bjones03", "czhang04"]


def test_approver_request_watches_the_pi_and_the_other_approvers(project_with_approvers):
    approver = get_user_model().objects.get(username="bjones03")

    assert tasks.project_watchers(project_with_approvers, approver) == ["asmith02", "czhang04"]


def test_administrator_request_watches_the_pi_and_every_approver(project_with_approvers, django_user_model):
    admin = django_user_model.objects.create_superuser(username="admin01", email="a@example.edu", password="pw")

    assert tasks.project_watchers(project_with_approvers, admin) == ["asmith02", "bjones03", "czhang04"]


def test_only_active_approvers_with_notifications_on_are_watchers(pi, project_with_approvers):
    add_member(project_with_approvers, "dlee05", role="User")
    add_member(project_with_approvers, "egarcia06", status="Removed")
    add_member(project_with_approvers, "fkim07", enable_notifications=False)

    assert tasks.project_watchers(project_with_approvers, pi) == ["bjones03", "czhang04"]


def test_the_pi_is_watched_even_without_notifications(project_with_approvers, pi):
    project_with_approvers.projectuser_set.filter(user=pi).update(enable_notifications=False)
    approver = get_user_model().objects.get(username="bjones03")

    assert tasks.project_watchers(project_with_approvers, approver) == ["asmith02", "czhang04"]


def test_approver_request_ticket_watches_the_pi_and_other_approvers(pi, project_with_approvers, use_client):
    allocation, _ = make_storage_allocation(pi)
    allocation.project = project_with_approvers
    allocation.save()
    approver = get_user_model().objects.get(username="czhang04")
    snow = use_client(FakeServiceNow())

    tasks.create_allocation_ticket(allocation.pk, requestor_pk=approver.pk)

    _, ticket = snow.incidents[f"coldfront:allocation:{allocation.pk}"]
    assert ticket["caller_id"] == "czhang04"
    assert ticket["watch_list"] == ["asmith02", "bjones03"]
    assert ticket["contact_type"] == "self-service"


def test_affected_client_is_the_caller_and_the_requestor_the_contact(
    pi, project_with_approvers, django_user_model, use_client
):
    allocation, _ = make_storage_allocation(pi)
    allocation.project = project_with_approvers
    allocation.save()
    admin = django_user_model.objects.create_superuser(username="admin01", email="a@example.edu", password="pw")
    approver = get_user_model().objects.get(username="bjones03")
    snow = use_client(FakeServiceNow())

    tasks.create_allocation_ticket(allocation.pk, requestor_pk=admin.pk, affected_pk=approver.pk)

    _, ticket = snow.incidents[f"coldfront:allocation:{allocation.pk}"]
    assert ticket["u_contact_person"] == "admin01"
    assert ticket["caller_id"] == "bjones03"
    assert ticket["watch_list"] == ["asmith02", "czhang04"]  # neither person already on the ticket
    assert ticket["contact_type"] == "email"  # follows the requestor, an administrator


def test_resolve_files_with_the_affected_client_as_caller(pi, requestor, use_client):
    change = make_storage_change(pi)
    client = use_client(FakeClient())

    tasks.resolve_allocation_change_ticket(change.pk, requestor_pk=requestor.pk, affected_pk=pi.pk)

    [ticket] = client.created
    assert ticket.u_contact_person == "jdoe01"
    assert ticket.caller_id == "asmith02"
    assert ticket.watch_list is None  # the PI is the caller, and there are no Approvers


# -- create_allocation_change_ticket -----------------------------------------------------------


class FakeServiceNow:
    """Remembers incidents by correlation_id, so a create -> resolve lifecycle can be followed."""

    def __init__(self):
        self.incidents = {}  # correlation_id -> (Incident, fields)

    def find_incident(self, correlation_id):
        found = self.incidents.get(correlation_id)
        return found[0] if found else None

    def sys_id_for(self, table, column, value):
        return f"<{table}.{column}={value}>"

    def create_incident(self, ticket):
        n = len(self.incidents) + 1
        incident = Incident(sys_id=f"i-{n}", number=f"INC{n:07d}", state=ticket.state or "2")
        self.incidents[ticket.correlation_id] = (incident, vars(ticket).copy())
        return incident

    def update_incident(self, sys_id, fields):
        for cid, (incident, stored) in self.incidents.items():
            if incident.sys_id == sys_id:
                stored.update(fields)
                self.incidents[cid] = (
                    Incident(sys_id, incident.number, state=fields.get("state", incident.state)),
                    stored,
                )


def test_request_opens_an_active_incident_for_admins(pi, requestor, use_client):
    change = make_storage_change(pi, new="20")
    # At request time the attribute still holds the current value.
    quota = change.allocationattributechangerequest_set.get().allocation_attribute
    quota.value = "10"
    quota.save()
    snow = use_client(FakeServiceNow())

    incident = tasks.create_allocation_change_ticket(change.pk, requestor_pk=requestor.pk)

    _, ticket = snow.incidents[f"coldfront:allocation-change:{change.pk}"]
    assert incident.number == "INC0000001"
    assert ticket["state"] is None  # left for the instance to choose
    assert ticket["assigned_to"] is None  # waits in the group queue
    assert ticket["short_description"] == "Research Storage: Quota Change Request | 20 TB | /cluster/tufts/lab"
    assert ticket["caller_id"] == "jdoe01" and ticket["watch_list"] == ["asmith02"]
    assert ticket["u_business_service"] == "Research Data Storage"
    assert ticket["u_action"] == "Move/Add/Change"
    description = ticket["description"]
    assert "- Storage Quota (TB): 10 -> 20" in description
    assert "Requested by: jdoe01 (jdoe01)" in description
    assert change.justification in description
    assert f"Review in ColdFront: {reverse('allocation-change-detail', kwargs={'pk': change.pk})}" in description


def test_request_rerun_returns_the_existing_incident(pi, use_client):
    change = make_storage_change(pi)
    snow = use_client(FakeServiceNow())

    first = tasks.create_allocation_change_ticket(change.pk)
    assert tasks.create_allocation_change_ticket(change.pk) == first
    assert len(snow.incidents) == 1


def test_unsupported_change_still_gets_a_request_ticket(pi, use_client):
    change = make_storage_change(pi)
    change.allocationattributechangerequest_set.all().delete()
    change.end_date_extension = 90
    change.save()
    snow = use_client(FakeServiceNow())

    tasks.create_allocation_change_ticket(change.pk)

    [(_, ticket)] = snow.incidents.values()
    assert (
        ticket["short_description"]
        == f"Research Storage: Allocation Change Request | {change.allocation.project.title}"
    )
    assert "- End date extension: 90 days" in ticket["description"]


def test_request_then_approval_resolves_the_same_incident(pi, requestor, use_client):
    change = make_storage_change(pi)
    snow = use_client(FakeServiceNow())

    opened = tasks.create_allocation_change_ticket(change.pk, requestor_pk=requestor.pk)
    resolved = tasks.resolve_allocation_change_ticket(change.pk, requestor_pk=requestor.pk)

    assert resolved == opened
    assert len(snow.incidents) == 1
    [(incident, fields)] = snow.incidents.values()
    assert incident.state == "6"
    assert fields["close_code"] == "Solved Remotely (Permanently)"
    assert "to 20 TB has been applied" in fields["comments"]
    assert "Quota Change Request" in fields["short_description"]  # the request's, not overwritten


# -- create_allocation_ticket / resolve_allocation_ticket --------------------------------------


def test_new_allocation_request_opens_an_active_incident_for_admins(pi, requestor, use_client):
    allocation, _ = make_storage_allocation(pi)
    snow = use_client(FakeServiceNow())

    incident = tasks.create_allocation_ticket(allocation.pk, requestor_pk=requestor.pk)

    _, ticket = snow.incidents[f"coldfront:allocation:{allocation.pk}"]
    assert incident.number == "INC0000001"
    assert ticket["state"] is None
    assert ticket["assigned_to"] is None
    assert ticket["short_description"] == (
        f"Research Storage: New Allocation Request | 10 TB | {allocation.project.title}"
    )
    assert ticket["caller_id"] == "jdoe01" and ticket["watch_list"] == ["asmith02"]
    assert ticket["u_action"] == "Activate/Setup/Install"
    assert ticket["u_business_service"] == "Research Data Storage"
    description = ticket["description"]
    assert "- Storage Quota (TB): 10" in description
    assert "- Storage Path: /cluster/tufts/lab" in description
    assert allocation.justification in description
    assert f"Review in ColdFront: {reverse('allocation-detail', kwargs={'pk': allocation.pk})}" in description


def test_every_new_allocation_gets_a_request_ticket(pi, use_client):
    allocation = make_cluster_allocation(pi)
    snow = use_client(FakeServiceNow())

    tasks.create_allocation_ticket(allocation.pk)

    [(_, ticket)] = snow.incidents.values()
    assert ticket["short_description"] == f"Research Cluster: New Allocation Request | {allocation.project.title}"
    assert ticket["u_business_service"] == "High Performance Computing (HPC)"  # Ticket default


def test_new_allocation_request_rerun_returns_the_existing_incident(pi, use_client):
    allocation, _ = make_storage_allocation(pi)
    snow = use_client(FakeServiceNow())

    first = tasks.create_allocation_ticket(allocation.pk)
    assert tasks.create_allocation_ticket(allocation.pk) == first
    assert len(snow.incidents) == 1


def test_request_then_activation_resolves_the_same_incident(pi, requestor, use_client):
    allocation, _ = make_storage_allocation(pi)
    snow = use_client(FakeServiceNow())

    opened = tasks.create_allocation_ticket(allocation.pk, requestor_pk=requestor.pk)
    resolved = tasks.resolve_allocation_ticket(allocation.pk)
    tasks.resolve_allocation_ticket(allocation.pk)  # re-run: already resolved

    assert resolved == opened
    [(incident, fields)] = snow.incidents.values()
    assert incident.state == "6"
    assert fields["close_code"] == "Solved Remotely (Permanently)"
    assert fields["close_notes"] == "Allocation activated."
    comments = fields["comments"]
    assert f"Your new allocation on Research Storage for project {allocation.project.title} is now active." in comments
    assert "Quota: 10 TB" in comments and "Path:  /cluster/tufts/lab" in comments
    assert fields["caller_id"] == "jdoe01"  # the request's people fields are kept


def test_activation_without_an_incident_files_a_resolved_one(pi, use_client):
    allocation, _ = make_storage_allocation(pi, path=None)
    snow = use_client(FakeServiceNow())

    tasks.resolve_allocation_ticket(allocation.pk)

    [(incident, ticket)] = snow.incidents.values()
    assert incident.state == "6"
    assert ticket["short_description"] == "Research Storage: New Allocation | 10 TB | " + allocation.project.title
    assert ticket["caller_id"] == "asmith02"  # no requestor: the PI
    assert ticket["u_action"] == "Activate/Setup/Install"
    assert "Path:" not in ticket["comments"]


# -- Staff requests are assigned to the admin who approved them --------------------------------


def submitted_by(in_request, submitter, pi):
    """A storage allocation requested by `submitter`, recorded in its history as a view would."""
    in_request(submitter)
    allocation, _ = make_storage_allocation(pi)
    allocation.status = f.AllocationStatusChoiceFactory(name="New")
    allocation.save()
    return allocation


def set_status(in_request, user, obj, status):
    in_request(user)
    obj.status = status
    obj.save()


def test_an_admins_request_is_filed_resolved_and_assigned_to_the_admin_who_activated_it(
    pi, ticketing_admin, admin, in_request, use_client
):
    allocation = submitted_by(in_request, ticketing_admin, pi)
    set_status(in_request, admin, allocation, f.AllocationStatusChoiceFactory(name="Active"))
    snow = use_client(FakeServiceNow())

    tasks.resolve_allocation_ticket(allocation.pk)

    [(incident, ticket)] = snow.incidents.values()
    assert incident.state == "6"
    assert ticket["assigned_to"] == "admin01"


def admin_change_with_linked_incident(pi, ticketing_admin, admin, in_request, snow, assigned_to=None):
    """A change request a ticketing admin submitted and `admin` approved, with a linked open incident."""
    in_request(ticketing_admin)
    change = make_storage_change(pi)
    set_status(in_request, admin, change, f.AllocationChangeStatusChoiceFactory(name="Approved"))
    cid = f"coldfront:allocation-change:{change.pk}"
    snow.incidents[cid] = (Incident("i-9", "INC0000009", "2", assigned_to=assigned_to), {})
    return change, cid


@pytest.mark.parametrize("assigned_to", [None, "u-someone"])
def test_an_admins_requests_linked_incident_is_assigned_to_the_approver(
    pi, ticketing_admin, admin, in_request, use_client, assigned_to
):
    snow = use_client(FakeServiceNow())
    change, cid = admin_change_with_linked_incident(pi, ticketing_admin, admin, in_request, snow, assigned_to)

    assert tasks.resolve_allocation_change_ticket(change.pk) == Incident("i-9", "INC0000009")
    _, fields = snow.incidents[cid]
    assert fields["state"] == "6"
    assert fields["assigned_to"] == "admin01"  # even over an assignee already set


class NoSuchUser(FakeServiceNow):
    """FakeServiceNow whose instance has no user `missing`, as ServiceNowClient reports it."""

    def __init__(self, missing):
        super().__init__()
        self.missing = missing

    def sys_id_for(self, table, column, value):
        if value == self.missing:
            raise UnknownReference(f"found no {table} with {column}={value}")
        return super().sys_id_for(table, column, value)


def test_an_approver_servicenow_doesnt_know_falls_back_to_the_service_account(
    pi, ticketing_admin, admin, in_request, use_client, caplog
):
    snow = use_client(NoSuchUser("admin01"))
    change, cid = admin_change_with_linked_incident(pi, ticketing_admin, admin, in_request, snow)

    tasks.resolve_allocation_change_ticket(change.pk)

    _, fields = snow.incidents[cid]
    assert fields["state"] == "6"
    assert fields["assigned_to"] == "svc_test"
    assert "ServiceNow has no user admin01" in caplog.text


def test_the_latest_activation_is_the_one_that_counts(pi, ticketing_admin, admin, in_request, django_user_model):
    allocation = submitted_by(in_request, ticketing_admin, pi)
    set_status(in_request, admin, allocation, f.AllocationStatusChoiceFactory(name="Active"))
    set_status(in_request, admin, allocation, f.AllocationStatusChoiceFactory(name="Inactive"))
    other = django_user_model.objects.create_superuser(username="admin02", password="pw")
    set_status(in_request, other, allocation, f.AllocationStatusChoiceFactory(name="Active"))
    set_status(in_request, admin, allocation, allocation.status)  # an edit while Active isn't an activation

    assert tasks.admin_request_approver(allocation, "Active") == "admin02"


def test_a_customers_request_goes_to_the_service_account(pi, requestor, admin, in_request, use_client):
    allocation = submitted_by(in_request, requestor, pi)
    set_status(in_request, admin, allocation, f.AllocationStatusChoiceFactory(name="Active"))
    snow = use_client(FakeServiceNow())

    tasks.resolve_allocation_ticket(allocation.pk)

    [(_, ticket)] = snow.incidents.values()
    assert ticket["assigned_to"] == "svc_test"


def test_without_history_users_there_is_no_approver(pi):
    allocation, _ = make_storage_allocation(pi)  # saved outside a request

    assert tasks.admin_request_approver(allocation, "Active") is None


def test_activation_of_an_unsupported_allocation_leaves_its_incident_open(pi, use_client):
    allocation = make_cluster_allocation(pi)
    snow = use_client(FakeServiceNow())

    tasks.create_allocation_ticket(allocation.pk)
    assert tasks.resolve_allocation_ticket(allocation.pk) is None

    [(incident, fields)] = snow.incidents.values()
    assert incident.state == "2"
    assert "comments" not in fields or fields["comments"] is None
