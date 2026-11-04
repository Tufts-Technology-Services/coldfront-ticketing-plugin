import datetime

import pytest
from coldfront.core.test_helpers import factories as f
from django.contrib.auth import get_user_model

from ticketing import client as servicenow
from ticketing import tasks
from ticketing.allocation_updates import (
    UnsupportedUpdate,
    describe_activation,
    describe_change,
    describe_new_allocation,
    describe_request,
    human_name,
    storage_will_provision,
)
from ticketing.client import IncidentRef, UnknownReference
from ticketing.tasks import people
from ticketing.tests.rendering import capture_templates

pytestmark = pytest.mark.django_db


def partial_template(name):
    return f"ticketing/partials/{name}.txt"


def make_storage_allocation(
    pi, *, resource_name="Research Storage", quota="10", path="/cluster/tufts/lab", requires_payment=True
):
    """A Storage allocation with a quota (a Storage Quota (TB) attribute) and a path; returns (allocation, quota).
    Its resource is paid for, as the storage tiers are, unless `requires_payment` is False."""
    resource = f.ResourceFactory(
        name=resource_name, resource_type=f.ResourceTypeFactory(name="Storage"), requires_payment=requires_payment
    )
    allocation = f.AllocationFactory(project=f.ProjectFactory(pi=pi))
    allocation.resources.add(resource)
    quota_attr = f.AllocationAttributeFactory(
        allocation=allocation,
        # Public, since customers see them; ColdFront's model default is private.
        allocation_attribute_type=f.AllocationAttributeTypeFactory(name="Storage Quota (TB)", is_private=False),
        value=quota,
    )
    if path:
        f.AllocationAttributeFactory(
            allocation=allocation,
            allocation_attribute_type=f.AllocationAttributeTypeFactory(
                name="Storage Path", attribute_type=f.AAttributeTypeFactory(name="Text"), is_private=False
            ),
            value=path,
        )
    return allocation, quota_attr


def make_storage_change(
    pi, *, resource_name="Research Storage", old="10", new="20", path="/cluster/tufts/lab", requires_payment=True
):
    """An approved Storage Quota (TB) change: the attribute already holds the new value."""
    allocation, quota = make_storage_allocation(
        pi, resource_name=resource_name, quota=old, path=path, requires_payment=requires_payment
    )
    change = f.AllocationChangeRequestFactory(allocation=allocation)
    f.AllocationAttributeChangeRequestFactory(
        allocation_change_request=change, allocation_attribute=quota, new_value=new
    )
    quota.value = new  # what approval does
    quota.save()
    return change


def make_cluster_allocation(pi):
    """An allocation on a resource type the storage plugin doesn't provision; it gets the generic message."""
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
        return IncidentRef(sys_id="i-1", number="INC0000001")

    def update_incident(self, sys_id, fields):
        self.updated.append((sys_id, fields))


@pytest.fixture
def use_client(monkeypatch):
    def install(client):
        monkeypatch.setattr(servicenow, "client_from_settings", lambda: client)
        return client

    return install


# -- describe_change ---------------------------------------------------------------------------


def test_storage_quota_change_message(pi):
    change = make_storage_change(pi)
    with capture_templates() as rendered:
        update = describe_change(change)

    assert update.short_description == "Research Storage: Quota Change | 20 TB | /cluster/tufts/lab"
    assert rendered.main == "ticketing/allocation_update/storage.txt"
    assert {partial_template("rcrm"), partial_template("charge_back"), partial_template("footer")} <= set(
        rendered.names
    )
    context = rendered.context("ticketing/allocation_update/storage.txt")
    assert (context["old_quota"], context["new_quota"]) == ("10 TB", "20 TB")
    assert context["path"] == "/cluster/tufts/lab"
    for value in ("10 TB", "20 TB", change.allocation.project.title, change.allocation.get_absolute_url()):
        assert value in update.comments
    assert update.business_service == "Research Data Storage"


def test_an_rstore_quota_change_is_given_its_smb_and_windows_paths(pi):
    change = make_storage_change(pi, resource_name="Tier 1 RStore", path=RSTORE_SMB_PATH)
    add_windows_path(change.allocation, RSTORE_WINDOWS_PATH)
    with capture_templates() as rendered:
        update = describe_change(change)

    assert update.short_description == f"Tier 1 RStore: Quota Change | 20 TB | {RSTORE_SMB_PATH}"
    context = rendered.context("ticketing/allocation_update/storage.txt")
    assert (context["path"], context["windows_path"]) == (RSTORE_SMB_PATH, RSTORE_WINDOWS_PATH)


def test_a_quota_change_with_neither_path_ends_with_the_project(pi):
    change = make_storage_change(pi, path=None)

    update = describe_change(change)

    assert update.short_description == f"Research Storage: Quota Change | 20 TB | {change.allocation.project.title}"


def test_resource_specific_template_wins_over_the_type_template(pi, settings, tmp_path):
    override = tmp_path / "ticketing" / "allocation_update" / "tier-1-rstore.txt"
    override.parent.mkdir(parents=True)
    override.write_text("RStore says {{ old_quota }} -> {{ new_quota }} on {{ path }}")
    settings.TEMPLATES = [{**settings.TEMPLATES[0], "DIRS": [tmp_path, *settings.TEMPLATES[0].get("DIRS", [])]}]

    update = describe_change(make_storage_change(pi, resource_name="Tier 1 RStore", path="rt-lab-share"))

    assert update.comments == "RStore says 10 TB -> 20 TB on rt-lab-share"


def test_an_aliased_resource_uses_the_template_of_the_resource_its_an_alias_of(pi, settings, tmp_path):
    override = tmp_path / "ticketing" / "allocation_update" / "tier-1-rstore.txt"
    override.parent.mkdir(parents=True)
    override.write_text("RStore says {{ new_quota }}")
    settings.TEMPLATES = [{**settings.TEMPLATES[0], "DIRS": [tmp_path, *settings.TEMPLATES[0].get("DIRS", [])]}]
    settings.TICKETING_RESOURCE_TEMPLATE_ALIASES = {"TTS Staff - Tier 1 RStore": "Tier 1 RStore"}

    update = describe_change(make_storage_change(pi, resource_name="TTS Staff - Tier 1 RStore"))

    assert update.comments == "RStore says 20 TB"


def test_a_storage_change_without_a_quota_change_gets_the_generic_message(pi):
    change = make_storage_change(pi)
    change.allocationattributechangerequest_set.all().delete()
    change.end_date_extension = 90
    change.save()

    with capture_templates() as rendered:
        update = describe_change(change)

    assert update.short_description == f"Research Storage: Allocation Change | {change.allocation.project.title}"
    assert rendered.main == "ticketing/allocation_change/generic.txt"  # not allocation_update/storage.txt
    assert rendered.main_context["change"].end_date_extension == 90
    assert update.business_service == "Research Data Storage"
    assert update.close_notes == "Allocation change applied."


def test_other_resource_types_get_the_generic_change_message(pi):
    change = make_storage_change(pi, old="10", new="20")
    resource = change.allocation.resources.first()
    resource.resource_type = f.ResourceTypeFactory(name="Cluster")
    resource.save()

    with capture_templates() as rendered:
        update = describe_change(change)

    assert rendered.main == "ticketing/allocation_change/generic.txt"
    assert rendered.main_context["attribute_changes"] == [{"name": "Storage Quota (TB)", "old": "10", "new": "20"}]
    assert update.business_service == "High Performance Computing (HPC)"


def cluster_change(pi, *private):
    """A change request on a Cluster allocation, changing Core Usage (Hours) 1000 -> 2000 and any
    `private` attributes (each to "secret")."""
    allocation = make_cluster_allocation(pi)
    change = f.AllocationChangeRequestFactory(allocation=allocation)
    for name, is_private, new in [("Core Usage (Hours)", False, "2000")] + [(n, True, "secret") for n in private]:
        attribute = f.AllocationAttributeFactory(
            allocation=allocation,
            allocation_attribute_type=f.AllocationAttributeTypeFactory(name=name, is_private=is_private),
            value="1000" if not is_private else "hidden",
        )
        f.AllocationAttributeChangeRequestFactory(
            allocation_change_request=change, allocation_attribute=attribute, new_value=new
        )
        attribute.value = new  # what approval does
        attribute.save()
    return change


def test_the_generic_change_message_leaves_out_private_attributes(pi):
    with capture_templates() as rendered:
        comments = describe_change(cluster_change(pi, "internal_quota")).comments

    assert rendered.context("ticketing/allocation_change/generic.txt")["attribute_changes"] == [
        {"name": "Core Usage (Hours)", "old": "1000", "new": "2000"}
    ]
    assert "internal_quota" not in comments and "secret" not in comments and "hidden" not in comments


def test_a_change_to_only_private_attributes_lists_nothing(pi):
    change = cluster_change(pi, "internal_quota")
    change.allocationattributechangerequest_set.filter(
        allocation_attribute__allocation_attribute_type__name="Core Usage (Hours)"
    ).delete()

    with capture_templates() as rendered:
        comments = describe_change(change).comments

    assert rendered.context("ticketing/allocation_change/generic.txt")["attribute_changes"] == []
    assert "internal_quota" not in comments and "secret" not in comments and "hidden" not in comments


def test_an_allocation_with_no_resource_is_unsupported(pi):
    change = make_storage_change(pi)
    change.allocation.resources.clear()

    with pytest.raises(UnsupportedUpdate, match="has no resource"):
        describe_change(change)


# -- human_name ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("slurm_account_name", "Slurm account name"),
        ("slurm-account-name", "Slurm account name"),
        ("slurmAccountName", "Slurm account name"),
        ("SlurmAccountName", "Slurm account name"),
        # known terms
        ("freeipa_group", "FreeIPA group"),
        ("share_creation_task_id", "Share creation task ID"),
        ("gpu_hours", "GPU hours"),
        ("cloud_account_2fa", "Cloud account 2FA"),
        ("parent_slurm_account", "Parent Slurm account"),
        ("utln_list", "Utln list"),  # site terms come from the setting
        ("unix_GID", "Unix GID"),
        ("video_id", "Video ID"),  # whole words only: "video" isn't touched
        # a run of capitals before a capitalized word
        ("SupportersQOSExpireDate", "Supporters QOS expire date"),
        ("SupportersQOS", "Supporters QOS"),
        ("GPUHours", "GPU hours"),
        # all in capitals: ordinary words, known terms still written their way
        ("CLOUD_USAGE_NOTIFICATION", "Cloud usage notification"),
        ("SLURM_GPU_LIMIT", "Slurm GPU limit"),
        # other capitals kept
        ("unix_XYZ", "Unix XYZ"),
        ("Storage_Group_Name", "Storage group name"),
        ("reported_quota_bytes", "Reported quota bytes"),
        ("slurm_user_specs_attriblist", "Slurm user specs attriblist"),
        # already for people, or nothing to split: left alone
        ("Storage Quota (TB)", "Storage Quota (TB)"),
        ("Core Usage (Hours)", "Core Usage (Hours)"),
        ("EXPIRE NOTIFICATION", "EXPIRE NOTIFICATION"),
        ("quota", "quota"),
        ("GPU", "GPU"),
        ("_", "_"),
    ],
)
def test_human_name(name, expected):
    assert human_name(name) == expected


def test_sites_add_known_terms(settings):
    settings.TICKETING_KNOWN_TERMS = ["UTLN", "RStore", "NetApp", "XSEDE"]

    assert human_name("rstore_share") == "RStore share"
    assert human_name("pi_utln") == "PI UTLN"
    assert human_name("netapp_volume") == "NetApp volume"
    assert human_name("xsede_allocation_id") == "XSEDE allocation ID"  # and the defaults still apply


def test_sites_respell_a_default_term(settings):
    settings.TICKETING_KNOWN_TERMS = ["SLURM"]

    assert human_name("parent_slurm_account") == "Parent SLURM account"


# -- storage_will_provision ----------------------------------------------------------------------


def test_storage_provisions_a_storage_allocation_and_its_quota_changes(pi):
    change = make_storage_change(pi)

    assert storage_will_provision(change.allocation)
    assert storage_will_provision(change.allocation, change)


def test_storage_doesnt_apply_a_change_without_a_quota_change(pi):
    change = make_storage_change(pi)
    change.allocationattributechangerequest_set.all().delete()

    assert not storage_will_provision(change.allocation, change)


def test_storage_doesnt_provision_other_resource_types_or_none(pi):
    assert not storage_will_provision(make_cluster_allocation(pi))
    assert not storage_will_provision(f.AllocationFactory(project=f.ProjectFactory(pi=pi)))


# -- resolve_allocation_change_ticket ----------------------------------------------------------


def test_resolves_the_open_incident_for_the_change(pi, use_client):
    change = make_storage_change(pi)
    client = use_client(FakeClient(existing=IncidentRef(sys_id="i-0", number="INC0000000", state="2")))

    assert tasks.resolve_allocation_change_ticket(change.pk) == IncidentRef(sys_id="i-0", number="INC0000000")
    assert client.looked_up == f"coldfront:allocation-change:{change.pk}"
    [(sys_id, fields)] = client.updated
    assert sys_id == "i-0"
    assert fields["state"] == "6"
    assert fields["close_code"] == "Solved Remotely (Permanently)"
    assert fields["close_notes"] == "Quota updated."
    assert fields["assigned_to"] == "svc_test"  # no one had picked it up
    assert "20 TB" in fields["comments"]
    assert client.created == []


def test_resolving_keeps_the_assignee_an_admin_set(pi, use_client):
    change = make_storage_change(pi)
    picked_up = IncidentRef(sys_id="i-0", number="INC0000000", state="2", assigned_to="u-admin")
    client = use_client(FakeClient(existing=picked_up))

    tasks.resolve_allocation_change_ticket(change.pk)

    [(_, fields)] = client.updated
    assert "assigned_to" not in fields
    assert fields["state"] == "6"


def test_already_resolved_incident_is_not_commented_on_again(pi, use_client):
    change = make_storage_change(pi)
    client = use_client(FakeClient(existing=IncidentRef(sys_id="i-0", number="INC0000000", state="6")))

    tasks.resolve_allocation_change_ticket(change.pk)

    assert client.updated == [] and client.created == []


def test_files_a_resolved_incident_when_none_exists(pi, requestor, use_client):
    change = make_storage_change(pi)
    client = use_client(FakeClient())

    tasks.resolve_allocation_change_ticket(change.pk, requestor_pk=requestor.pk)

    [ticket] = client.created
    assert ticket.caller_id == "asmith02"  # the PI is always the Affected Client
    assert ticket.u_contact_person == "jdoe01"
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
    change.allocation.resources.clear()
    client = use_client(FakeClient(existing=IncidentRef(sys_id="i-0", number="INC0000000", state="2")))

    assert tasks.resolve_allocation_change_ticket(change.pk) is None
    assert client.updated == [] and client.created == []


# -- contact_type_for --------------------------------------------------------------------------


def test_contact_type_pi_and_manager_are_self_service(pi, requestor):
    project = f.ProjectFactory(pi=pi)
    f.ProjectUserFactory(project=project, user=requestor, role=f.ProjectUserRoleChoiceFactory(name="Manager"))

    assert people.contact_type_for(pi, project) == "self-service"
    assert people.contact_type_for(requestor, project) == "self-service"


def test_contact_type_superuser_on_someone_elses_project_is_email(pi, django_user_model):
    admin = django_user_model.objects.create_superuser(username="admin01", email="a@example.edu", password="pw")

    assert people.contact_type_for(admin, f.ProjectFactory(pi=pi)) == "email"


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
    assert people.project_watchers(project_with_approvers, pi) == ["bjones03", "czhang04"]


def test_approver_request_watches_the_pi_and_the_other_approvers(project_with_approvers):
    approver = get_user_model().objects.get(username="bjones03")

    assert people.project_watchers(project_with_approvers, approver) == ["asmith02", "czhang04"]


def test_administrator_request_watches_the_pi_and_every_approver(project_with_approvers, django_user_model):
    admin = django_user_model.objects.create_superuser(username="admin01", email="a@example.edu", password="pw")

    assert people.project_watchers(project_with_approvers, admin) == ["asmith02", "bjones03", "czhang04"]


def test_only_active_approvers_with_notifications_on_are_watchers(pi, project_with_approvers):
    add_member(project_with_approvers, "dlee05", role="User")
    add_member(project_with_approvers, "egarcia06", status="Removed")
    add_member(project_with_approvers, "fkim07", enable_notifications=False)

    assert people.project_watchers(project_with_approvers, pi) == ["bjones03", "czhang04"]


def test_the_pi_is_watched_even_without_notifications(project_with_approvers, pi):
    project_with_approvers.projectuser_set.filter(user=pi).update(enable_notifications=False)
    approver = get_user_model().objects.get(username="bjones03")

    assert people.project_watchers(project_with_approvers, approver) == ["asmith02", "czhang04"]


def test_approver_request_ticket_watches_the_pi_and_other_approvers(pi, project_with_approvers, use_client):
    allocation, _ = make_storage_allocation(pi)
    allocation.project = project_with_approvers
    allocation.save()
    approver = get_user_model().objects.get(username="czhang04")
    snow = use_client(FakeServiceNow())

    tasks.create_allocation_ticket(allocation.pk, requestor_pk=approver.pk)

    _, ticket = snow.incidents[f"coldfront:allocation:{allocation.pk}"]
    assert ticket["caller_id"] == "asmith02" and ticket["u_contact_person"] == "czhang04"
    assert ticket["watch_list"] == ["asmith02", "bjones03"]
    assert ticket["contact_type"] == "self-service"


def test_an_admins_request_has_the_pi_as_caller_and_the_admin_as_contact(
    pi, project_with_approvers, django_user_model, use_client
):
    allocation, _ = make_storage_allocation(pi)
    allocation.project = project_with_approvers
    allocation.save()
    admin = django_user_model.objects.create_superuser(username="admin01", email="a@example.edu", password="pw")
    snow = use_client(FakeServiceNow())

    tasks.create_allocation_ticket(allocation.pk, requestor_pk=admin.pk)

    _, ticket = snow.incidents[f"coldfront:allocation:{allocation.pk}"]
    assert ticket["caller_id"] == "asmith02"
    assert ticket["u_contact_person"] == "admin01"
    # ServiceNow emails the contact person and watch list, not the caller, so the PI watches too
    assert ticket["watch_list"] == ["asmith02", "bjones03", "czhang04"]
    assert ticket["contact_type"] == "email"  # follows the requestor, an administrator


# -- create_allocation_change_ticket -----------------------------------------------------------


class FakeServiceNow:
    """Remembers incidents by correlation_id, so a create -> resolve lifecycle can be followed."""

    def __init__(self):
        self.incidents = {}  # correlation_id -> (IncidentRef, fields)

    def find_incident(self, correlation_id):
        found = self.incidents.get(correlation_id)
        return found[0] if found else None

    def sys_id_for(self, table, column, value):
        return f"<{table}.{column}={value}>"

    def create_incident(self, ticket):
        n = len(self.incidents) + 1
        incident = IncidentRef(sys_id=f"i-{n}", number=f"INC{n:07d}", state=ticket.state or "2")
        self.incidents[ticket.correlation_id] = (incident, vars(ticket).copy())
        return incident

    def update_incident(self, sys_id, fields):
        for cid, (incident, stored) in self.incidents.items():
            if incident.sys_id == sys_id:
                stored.update(fields)
                self.incidents[cid] = (
                    IncidentRef(sys_id, incident.number, state=fields.get("state", incident.state)),
                    stored,
                )


def test_request_opens_an_active_incident_for_admins(pi, requestor, use_client):
    change = make_storage_change(pi, new="20")
    # At request time the attribute still holds the current value.
    quota = change.allocationattributechangerequest_set.get().allocation_attribute
    quota.value = "10"
    quota.save()
    snow = use_client(FakeServiceNow())

    with capture_templates() as rendered:
        incident = tasks.create_allocation_change_ticket(change.pk, requestor_pk=requestor.pk)

    _, ticket = snow.incidents[f"coldfront:allocation-change:{change.pk}"]
    assert incident.number == "INC0000001"
    assert ticket["state"] is None  # left for the instance to choose
    assert ticket["assigned_to"] is None  # waits in the group queue
    assert ticket["short_description"] == "Research Storage: Quota Change Request | 20 TB | /cluster/tufts/lab"
    assert ticket["caller_id"] == "asmith02" and ticket["u_contact_person"] == "jdoe01"
    assert ticket["watch_list"] == ["asmith02"]
    assert ticket["u_business_service"] == "Research Data Storage"
    assert ticket["u_action"] == "Move/Add/Change"
    context = rendered.context("ticketing/allocation_change_request.txt")
    assert context["attribute_changes"] == [{"name": "Storage Quota (TB)", "current": "10", "requested": "20"}]
    assert (context["pi"].username, context["requestor"].username) == ("asmith02", "jdoe01")
    description = ticket["description"]
    assert change.justification in description
    assert change.get_absolute_url() in description


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

    with capture_templates() as rendered:
        tasks.create_allocation_change_ticket(change.pk)

    [(_, ticket)] = snow.incidents.values()
    assert (
        ticket["short_description"]
        == f"Research Storage: Allocation Change Request | {change.allocation.project.title}"
    )
    context = rendered.context("ticketing/allocation_change_request.txt")
    assert (context["attribute_changes"], context["change"].end_date_extension) == ([], 90)


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
    assert "10 TB" in fields["comments"] and "20 TB" in fields["comments"]
    assert "Quota Change Request" in fields["short_description"]  # the request's, not overwritten


# -- create_allocation_ticket / resolve_allocation_ticket --------------------------------------


def test_new_allocation_request_opens_an_active_incident_for_admins(pi, requestor, use_client):
    allocation, _ = make_storage_allocation(pi)
    snow = use_client(FakeServiceNow())

    with capture_templates() as rendered:
        incident = tasks.create_allocation_ticket(allocation.pk, requestor_pk=requestor.pk)

    _, ticket = snow.incidents[f"coldfront:allocation:{allocation.pk}"]
    assert incident.number == "INC0000001"
    assert ticket["state"] is None
    assert ticket["assigned_to"] is None
    assert ticket["short_description"] == (
        f"Research Storage: New Allocation Request | 10 TB | {allocation.project.title}"
    )
    assert ticket["caller_id"] == "asmith02" and ticket["u_contact_person"] == "jdoe01"
    assert ticket["watch_list"] == ["asmith02"]
    assert ticket["u_action"] == "Activate/Setup/Install"
    assert ticket["u_business_service"] == "Research Data Storage"
    assert rendered.context("ticketing/allocation_request.txt")["details"] == [
        f"Project:            {allocation.project.title}",
        "Owner:              asmith02 (asmith02)",
        "Requested by:       jdoe01 (jdoe01)",
        "Resource:           Research Storage",
        "Storage Path:       /cluster/tufts/lab",
        "Storage Quota (TB): 10",
    ]
    description = ticket["description"]
    assert "Quantity" not in description
    assert allocation.justification in description
    assert allocation.get_absolute_url() in description


def test_every_new_allocation_gets_a_request_ticket(pi, use_client):
    allocation = make_cluster_allocation(pi)
    snow = use_client(FakeServiceNow())

    tasks.create_allocation_ticket(allocation.pk)

    [(_, ticket)] = snow.incidents.values()
    assert ticket["short_description"] == f"Research Cluster: New Allocation Request | {allocation.project.title}"
    assert ticket["u_business_service"] == "High Performance Computing (HPC)"  # ServiceNowIncident default


def test_the_new_allocation_request_description_leaves_out_private_attributes(pi, requestor):
    allocation = make_cluster_allocation(pi)
    for name, value, private in [("slurm_account_name", "smith_lab", False), ("internal_note", "hidden", True)]:
        f.AllocationAttributeFactory(
            allocation=allocation,
            allocation_attribute_type=f.AllocationAttributeTypeFactory(name=name, is_private=private),
            value=value,
        )

    with capture_templates() as rendered:
        description = describe_new_allocation(allocation, requestor).description

    assert "Slurm account name: smith_lab" in rendered.context("ticketing/allocation_request.txt")["details"]
    assert "internal_note" not in description and "hidden" not in description


def test_the_change_request_description_leaves_out_private_attributes(pi, requestor):
    with capture_templates() as rendered:
        description = describe_request(cluster_change(pi, "internal_quota"), requestor).description

    # values as stored; approval already applied
    assert rendered.context("ticketing/allocation_change_request.txt")["attribute_changes"] == [
        {"name": "Core Usage (Hours)", "current": "2000", "requested": "2000"}
    ]
    assert "internal_quota" not in description and "secret" not in description


def test_a_change_request_of_only_private_attributes_lists_none(pi, requestor):
    change = cluster_change(pi, "internal_quota")
    change.allocationattributechangerequest_set.filter(
        allocation_attribute__allocation_attribute_type__name="Core Usage (Hours)"
    ).delete()

    with capture_templates() as rendered:
        description = describe_request(change, requestor).description

    assert rendered.context("ticketing/allocation_change_request.txt")["attribute_changes"] == []
    assert "internal_quota" not in description and "secret" not in description


def test_a_private_quota_still_names_the_quota_in_the_short_description(pi, requestor):
    change = make_storage_change(pi, old="10", new="20")
    quota_type = change.allocationattributechangerequest_set.get().allocation_attribute.allocation_attribute_type
    quota_type.is_private = True
    quota_type.save()

    request = describe_request(change, requestor)

    assert request.short_description == "Research Storage: Quota Change Request | 20 TB | /cluster/tufts/lab"
    assert "Storage Quota (TB)" not in request.description


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
    with capture_templates() as rendered:
        resolved = tasks.resolve_allocation_ticket(allocation.pk)
    tasks.resolve_allocation_ticket(allocation.pk)  # re-run: already resolved

    assert resolved == opened
    [(incident, fields)] = snow.incidents.values()
    assert incident.state == "6"
    assert fields["close_code"] == "Solved Remotely (Permanently)"
    assert fields["close_notes"] == "Allocation activated."
    assert rendered.main == "ticketing/allocation_new/storage.txt"
    comments = fields["comments"]
    assert allocation.project.title in comments
    assert "10 TB" in comments and "/cluster/tufts/lab" in comments
    assert fields["u_contact_person"] == "jdoe01"  # the request's people fields are kept


def test_activation_without_an_incident_files_a_resolved_one(pi, use_client):
    allocation, _ = make_storage_allocation(pi, path=None)
    snow = use_client(FakeServiceNow())

    tasks.resolve_allocation_ticket(allocation.pk)

    [(incident, ticket)] = snow.incidents.values()
    assert incident.state == "6"
    assert ticket["short_description"] == "Research Storage: New Allocation | 10 TB | " + allocation.project.title
    assert ticket["caller_id"] == "asmith02"  # no requestor: the PI
    assert ticket["u_action"] == "Activate/Setup/Install"


RSTORE_SMB_PATH = "smb://rstore.it.tufts.edu/smith_lab$"
RSTORE_WINDOWS_PATH = "\\\\rstore.it.tufts.edu\\smith_lab$"


def add_windows_path(allocation, value):
    f.AllocationAttributeFactory(
        allocation=allocation,
        allocation_attribute_type=f.AllocationAttributeTypeFactory(
            name="Storage Path (Windows)", attribute_type=f.AAttributeTypeFactory(name="Text"), is_private=False
        ),
        value=value,
    )


@pytest.mark.parametrize(
    "resource_name, template",
    [
        ("Tier 1 HPC", "tier-1-hpc"),
        ("Tier 1 RStore", "tier-1-rstore"),
        ("Tier 2 Research", "tier-2-research"),
    ],
)
def test_each_storage_tier_has_its_own_new_allocation_message(pi, resource_name, template):
    allocation, _ = make_storage_allocation(pi, resource_name=resource_name)

    with capture_templates() as rendered:
        comments = describe_activation(allocation).comments

    assert rendered.main == f"ticketing/allocation_new/{template}.txt"
    assert {partial_template("rcrm"), partial_template("charge_back"), partial_template("footer")} <= set(
        rendered.names
    )
    assert allocation.project.title in comments
    assert "/cluster/tufts/lab" in comments and "10 TB" in comments


def test_a_staff_copy_of_a_tier_gets_the_tiers_message_through_an_alias(pi, settings):
    settings.TICKETING_RESOURCE_TEMPLATE_ALIASES = {"TTS Staff - Tier 1 HPC": "Tier 1 HPC"}
    allocation, _ = make_storage_allocation(pi, resource_name="TTS Staff - Tier 1 HPC")

    with capture_templates() as rendered:
        describe_activation(allocation)

    assert rendered.main == "ticketing/allocation_new/tier-1-hpc.txt"


def test_a_storage_resource_without_its_own_message_gets_the_storage_one(pi):
    allocation, _ = make_storage_allocation(pi, resource_name="Tier 3 Research", path=None)

    with capture_templates() as rendered:
        comments = describe_activation(allocation).comments

    assert rendered.main == "ticketing/allocation_new/storage.txt"
    assert rendered.main_context["path"] == ""
    assert "Tier 3 Research" in comments and "10 TB" in comments


def test_a_new_rstore_allocation_lists_its_smb_paths_for_mac_and_windows(pi):
    allocation, _ = make_storage_allocation(pi, resource_name="Tier 1 RStore", path=RSTORE_SMB_PATH)
    add_windows_path(allocation, RSTORE_WINDOWS_PATH)

    update = describe_activation(allocation)

    assert RSTORE_SMB_PATH in update.comments and RSTORE_WINDOWS_PATH in update.comments
    assert update.comments.count(RSTORE_SMB_PATH) == 1  # listed once, as the Mac path
    assert "10 TB" in update.comments
    assert update.short_description == f"Tier 1 RStore: New Allocation | 10 TB | {RSTORE_SMB_PATH}"


def test_a_new_rstore_allocation_without_a_windows_path_lists_only_the_mac_one(pi):
    allocation, _ = make_storage_allocation(pi, resource_name="Tier 1 RStore", path=RSTORE_SMB_PATH)

    with capture_templates() as rendered:
        comments = describe_activation(allocation).comments

    assert RSTORE_SMB_PATH in comments
    assert rendered.context("ticketing/allocation_new/tier-1-rstore.txt")["windows_path"] == ""


def add_group(allocation, value):
    f.AllocationAttributeFactory(
        allocation=allocation,
        allocation_attribute_type=f.AllocationAttributeTypeFactory(
            name="Group", attribute_type=f.AAttributeTypeFactory(name="Text")
        ),
        value=value,
    )


def test_course_storage_explains_the_class_folders_and_names_its_groups(pi):
    allocation, _ = make_storage_allocation(
        pi, resource_name="Course - Tier 1 HPC", path="/cluster/tufts/bio101", requires_payment=False
    )
    add_group(allocation, "bio101")

    with capture_templates() as rendered:
        comments = describe_activation(allocation).comments

    assert rendered.main == "ticketing/allocation_new/course-tier-1-hpc.txt"
    context = rendered.main_context
    assert (context["group"], context["admin_group"]) == ("bio101", "bio101admin")
    for value in (allocation.project.title, "/cluster/tufts/bio101", "10 TB", "bio101admin", "(bio101)"):
        assert value in comments
    assert partial_template("charge_back") not in rendered.names  # course storage isn't paid for


def test_course_storage_takes_its_group_from_the_project_without_one_of_its_own(pi):
    allocation, _ = make_storage_allocation(pi, resource_name="Course - Tier 1 HPC")
    group_type = f.ProjectAttributeTypeFactory(name="Group")
    f.ProjectAttributeFactory(project=allocation.project, proj_attr_type=group_type, value="chem202")

    with capture_templates() as rendered:
        describe_activation(allocation)

    context = rendered.context("ticketing/allocation_new/course-tier-1-hpc.txt")
    assert (context["group"], context["admin_group"]) == ("chem202", "chem202admin")


def test_course_storage_without_a_group_or_path_still_gets_the_course_message(pi):
    allocation, _ = make_storage_allocation(pi, resource_name="Course - Tier 1 HPC", path=None)

    with capture_templates() as rendered:
        describe_activation(allocation)

    assert rendered.main == "ticketing/allocation_new/course-tier-1-hpc.txt"
    context = rendered.main_context
    assert (context["group"], context["admin_group"], context["path"]) == (None, None, "")


def test_storage_nobody_pays_for_leaves_out_the_charge_back(pi):
    allocation, _ = make_storage_allocation(pi, resource_name="Tier 1 HPC", requires_payment=False)
    change = make_storage_change(pi, requires_payment=False)

    with capture_templates() as rendered:
        describe_activation(allocation)
        describe_change(change)

    assert partial_template("charge_back") not in rendered.names
    assert rendered.names.count(partial_template("rcrm")) == 2


def test_a_new_rstore_allocation_without_paths_leaves_them_out(pi):
    allocation, _ = make_storage_allocation(pi, resource_name="Tier 1 RStore", path=None)

    with capture_templates() as rendered:
        comments = describe_activation(allocation).comments

    context = rendered.context("ticketing/allocation_new/tier-1-rstore.txt")
    assert (context["path"], context["windows_path"]) == ("", "")
    assert "\n\n\n" not in comments  # no stray blank lines where they would have been


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
    snow.incidents[cid] = (IncidentRef("i-9", "INC0000009", "2", assigned_to=assigned_to), {})
    return change, cid


@pytest.mark.parametrize("assigned_to", [None, "u-someone"])
def test_an_admins_requests_linked_incident_is_assigned_to_the_approver(
    pi, ticketing_admin, admin, in_request, use_client, assigned_to
):
    snow = use_client(FakeServiceNow())
    change, cid = admin_change_with_linked_incident(pi, ticketing_admin, admin, in_request, snow, assigned_to)

    assert tasks.resolve_allocation_change_ticket(change.pk) == IncidentRef("i-9", "INC0000009")
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

    assert people.admin_request_approver(allocation, "Active") == "admin02"


def test_a_customers_request_goes_to_the_service_account(pi, requestor, admin, in_request, use_client):
    allocation = submitted_by(in_request, requestor, pi)
    set_status(in_request, admin, allocation, f.AllocationStatusChoiceFactory(name="Active"))
    snow = use_client(FakeServiceNow())

    tasks.resolve_allocation_ticket(allocation.pk)

    [(_, ticket)] = snow.incidents.values()
    assert ticket["assigned_to"] == "svc_test"


def test_without_history_users_there_is_no_approver(pi):
    allocation, _ = make_storage_allocation(pi)  # saved outside a request

    assert people.admin_request_approver(allocation, "Active") is None


def test_activation_of_a_non_storage_allocation_resolves_with_the_generic_message(pi, use_client):
    allocation = make_cluster_allocation(pi)
    snow = use_client(FakeServiceNow())

    tasks.create_allocation_ticket(allocation.pk)
    with capture_templates() as rendered:
        tasks.resolve_allocation_ticket(allocation.pk)

    [(incident, fields)] = snow.incidents.values()
    assert incident.state == "6"
    assert rendered.main == "ticketing/allocation_new/generic.txt"
    assert "Research Cluster" in fields["comments"] and allocation.project.title in fields["comments"]
    assert fields["close_notes"] == "Allocation activated."


def test_the_generic_activation_message_shows_the_end_date_and_public_attributes(pi):
    allocation = make_cluster_allocation(pi)
    allocation.end_date = datetime.date(2027, 6, 30)
    allocation.save()
    for name, value, private in [
        ("slurm_account_name", "smith_lab", False),
        ("Core Usage (Hours)", "5000", False),
        ("internal_note", "do not show", True),
    ]:
        f.AllocationAttributeFactory(
            allocation=allocation,
            allocation_attribute_type=f.AllocationAttributeTypeFactory(name=name, is_private=private),
            value=value,
        )

    with capture_templates() as rendered:
        comments = describe_activation(allocation).comments

    assert rendered.context("ticketing/allocation_new/generic.txt")["attributes"] == [
        {"name": "Core Usage (Hours)", "value": "5000"},
        {"name": "Slurm account name", "value": "smith_lab"},
    ]
    assert rendered.main_context["allocation"].end_date == datetime.date(2027, 6, 30)
    assert "5000" in comments and "smith_lab" in comments
    assert "internal_note" not in comments and "do not show" not in comments


def test_the_generic_activation_message_without_attributes_has_none_to_list(pi):
    with capture_templates() as rendered:
        describe_activation(make_cluster_allocation(pi))

    assert rendered.context("ticketing/allocation_new/generic.txt")["attributes"] == []


# -- Customer messages link the allocation's page ------------------------------------------------


def storage_activation(pi, resource_name):
    allocation, _ = make_storage_allocation(pi, resource_name=resource_name)
    return allocation, describe_activation(allocation)


def cluster_activation(pi):
    allocation = make_cluster_allocation(pi)
    return allocation, describe_activation(allocation)


def storage_quota_change(pi):
    change = make_storage_change(pi)
    return change.allocation, describe_change(change)


def cluster_attribute_change(pi):
    change = cluster_change(pi)
    return change.allocation, describe_change(change)


@pytest.mark.parametrize(
    "describe",
    [
        *(
            pytest.param(lambda pi, name=name: storage_activation(pi, name), id=f"new {name}")
            for name in ["Tier 1 HPC", "Course - Tier 1 HPC", "Tier 1 RStore", "Tier 2 Research", "Tier 3 Research"]
        ),
        pytest.param(storage_quota_change, id="quota change"),
        pytest.param(cluster_activation, id="new generic"),
        pytest.param(cluster_attribute_change, id="generic change"),
    ],
)
def test_a_customer_message_links_the_allocations_page(pi, describe):
    with capture_templates() as rendered:
        allocation, update = describe(pi)

    assert partial_template("allocation_link") in rendered.names
    assert allocation.get_absolute_url() in update.comments
