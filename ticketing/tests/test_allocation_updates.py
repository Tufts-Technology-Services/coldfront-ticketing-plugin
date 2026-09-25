import pytest
from coldfront.core.test_helpers import factories as f
from django.urls import reverse

from ticketing import tasks
from ticketing.allocation_updates import UnsupportedUpdate, describe_change
from ticketing.client import Incident

pytestmark = pytest.mark.django_db


def make_storage_change(pi, *, resource_name="Research Storage", old="10", new="20", path="/cluster/tufts/lab"):
    """An approved Storage Quota (TB) change: the attribute already holds the new value."""
    resource = f.ResourceFactory(name=resource_name, resource_type=f.ResourceTypeFactory(name="Storage"))
    allocation = f.AllocationFactory(project=f.ProjectFactory(pi=pi))
    allocation.resources.add(resource)
    quota = f.AllocationAttributeFactory(
        allocation=allocation,
        allocation_attribute_type=f.AllocationAttributeTypeFactory(name="Storage Quota (TB)"),
        value=old,
    )
    f.AllocationAttributeFactory(
        allocation=allocation,
        allocation_attribute_type=f.AllocationAttributeTypeFactory(
            name="Storage Path", attribute_type=f.AAttributeTypeFactory(name="Text")
        ),
        value=path,
    )
    change = f.AllocationChangeRequestFactory(allocation=allocation)
    f.AllocationAttributeChangeRequestFactory(
        allocation_change_request=change, allocation_attribute=quota, new_value=new
    )
    quota.value = new  # what approval does
    quota.save()
    return change


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
    assert "to 20 TB" in fields["comments"]
    assert client.created == []


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


# -- create_allocation_change_ticket -----------------------------------------------------------


class FakeServiceNow:
    """Remembers incidents by correlation_id, so a create -> resolve lifecycle can be followed."""

    def __init__(self):
        self.incidents = {}  # correlation_id -> (Incident, fields)

    def find_incident(self, correlation_id):
        found = self.incidents.get(correlation_id)
        return found[0] if found else None

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
    assert ticket["state"] is None  # left for the instance to open as Active
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
