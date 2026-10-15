import datetime
import logging

import pytest
from coldfront.core.test_helpers import factories as f
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from ticketing import tasks
from ticketing.client import Incident
from ticketing.models import FiledTicket
from ticketing.tests.test_allocation_updates import FakeServiceNow, make_storage_allocation, make_storage_change

pytestmark = pytest.mark.django_db

SNOW = "https://servicenow.invalid"


class CountingServiceNow(FakeServiceNow):
    """FakeServiceNow that remembers which correlation_ids were looked up."""

    def __init__(self):
        super().__init__()
        self.looked_up = []

    def find_incident(self, correlation_id):
        self.looked_up.append(correlation_id)
        return super().find_incident(correlation_id)


@pytest.fixture
def snow(monkeypatch):
    client = CountingServiceNow()
    monkeypatch.setattr(tasks, "client_from_settings", lambda: client)
    return client


def file_ticket(correlation_id, kind=FiledTicket.Kind.ALLOCATION, number="INC0000009", **subjects):
    return FiledTicket.objects.create(
        correlation_id=correlation_id, kind=kind, number=number, sys_id="abc123", **subjects
    )


# -- The model -----------------------------------------------------------------------------------


@override_settings(SERVICENOW_URL=f"{SNOW}/")
def test_url_points_at_the_configured_instance():
    ticket = FiledTicket(table="incident", sys_id="abc123")

    assert ticket.url == f"{SNOW}/nav_to.do?uri=incident.do%3Fsys_id%3Dabc123"


def test_an_allocations_tickets_include_its_change_requests(pi):
    change = make_storage_change(pi)
    allocation = change.allocation
    request = file_ticket("coldfront:allocation:1", allocation=allocation)
    changed = file_ticket("coldfront:allocation-change:1", FiledTicket.Kind.ALLOCATION_CHANGE, allocation=allocation)
    file_ticket("coldfront:allocation:2", allocation=make_storage_allocation(pi)[0])

    assert set(FiledTicket.objects.about(allocation)) == {request, changed}


def test_a_projects_tickets_include_its_allocations(pi):
    allocation, _ = make_storage_allocation(pi)
    project = allocation.project
    users = file_ticket("coldfront:project-users:1:2026-09-28", FiledTicket.Kind.PROJECT_USERS, project=project)
    request = file_ticket("coldfront:allocation:1", allocation=allocation)
    file_ticket("coldfront:project-users:2:2026-09-28", FiledTicket.Kind.PROJECT_USERS, project=f.ProjectFactory(pi=pi))

    assert set(FiledTicket.objects.about(project)) == {users, request}


def test_a_users_tickets_are_their_hpc_account(requestor):
    account = file_ticket("coldfront:hpc-account:jdoe01", FiledTicket.Kind.HPC_ACCOUNT, user=requestor)

    assert list(FiledTicket.objects.about(requestor)) == [account]


def test_tickets_about_anything_else_is_an_error():
    with pytest.raises(TypeError):
        FiledTicket.objects.about("allocation 7")


def test_deleting_the_subject_keeps_the_record(requestor):
    file_ticket("coldfront:hpc-account:jdoe01", FiledTicket.Kind.HPC_ACCOUNT, user=requestor)

    requestor.delete()

    ticket = FiledTicket.objects.get()
    assert ticket.user is None
    assert ticket.correlation_id == "coldfront:hpc-account:jdoe01"


# -- Tasks record what they file -----------------------------------------------------------------


def test_opening_a_request_records_it(pi, snow):
    allocation, _ = make_storage_allocation(pi)

    incident = tasks.create_allocation_ticket(allocation.pk)

    ticket = FiledTicket.objects.get()
    assert (ticket.number, ticket.sys_id) == (incident.number, incident.sys_id)
    assert ticket.correlation_id == f"coldfront:allocation:{allocation.pk}"
    assert ticket.kind == FiledTicket.Kind.ALLOCATION
    assert ticket.allocation == allocation
    assert ticket.resolved_at is None


def test_a_recorded_request_is_not_looked_up_in_servicenow(pi, snow):
    allocation, _ = make_storage_allocation(pi)
    first = tasks.create_allocation_ticket(allocation.pk)
    snow.looked_up.clear()

    assert tasks.create_allocation_ticket(allocation.pk) == first
    assert snow.looked_up == []
    assert len(snow.incidents) == 1


def test_one_found_in_servicenow_but_not_recorded_is_recorded(pi, snow):
    allocation, _ = make_storage_allocation(pi)
    filed = tasks.create_allocation_ticket(allocation.pk)
    FiledTicket.objects.all().delete()

    assert tasks.create_allocation_ticket(allocation.pk) == filed
    assert FiledTicket.objects.get().number == filed.number
    assert len(snow.incidents) == 1


def test_resolving_stamps_the_record(pi, snow):
    allocation, _ = make_storage_allocation(pi)
    tasks.create_allocation_ticket(allocation.pk)

    tasks.resolve_allocation_ticket(allocation.pk)

    ticket = FiledTicket.objects.get()
    assert ticket.resolved_at is not None
    assert ticket.allocation == allocation


def test_resolving_without_an_open_incident_records_the_one_filed_resolved(pi, snow):
    allocation, _ = make_storage_allocation(pi)

    incident = tasks.resolve_allocation_ticket(allocation.pk)

    ticket = FiledTicket.objects.get()
    assert ticket.number == incident.number
    assert ticket.resolved_at is not None


def test_one_resolved_by_someone_else_is_recorded_unstamped(pi, snow):
    allocation, _ = make_storage_allocation(pi)
    tasks.create_allocation_ticket(allocation.pk)
    FiledTicket.objects.all().delete()
    [(incident, fields)] = snow.incidents.values()
    snow.incidents[f"coldfront:allocation:{allocation.pk}"] = (Incident(incident.sys_id, incident.number, "6"), fields)

    tasks.resolve_allocation_ticket(allocation.pk)

    assert FiledTicket.objects.get().resolved_at is None


def test_a_change_request_is_recorded_against_the_change_and_its_allocation(pi, snow):
    change = make_storage_change(pi)

    tasks.create_allocation_change_ticket(change.pk)

    ticket = FiledTicket.objects.get()
    assert ticket.kind == FiledTicket.Kind.ALLOCATION_CHANGE
    assert ticket.allocation_change == change
    assert ticket.allocation == change.allocation


def test_an_hpc_account_is_recorded_against_the_user(requestor, snow):
    tasks.create_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")
    tasks.resolve_hpc_account_ticket("jdoe01", "/cluster/home/jdoe01")

    ticket = FiledTicket.objects.get()
    assert ticket.kind == FiledTicket.Kind.HPC_ACCOUNT
    assert ticket.user == requestor
    assert ticket.resolved_at is not None


def test_a_project_users_notice_is_recorded_resolved_and_spares_the_next_lookup(pi, snow):
    project = f.ProjectFactory(pi=pi)

    first = tasks.create_project_users_ticket(project.pk)
    snow.looked_up.clear()
    again = tasks.create_project_users_ticket(project.pk)

    ticket = FiledTicket.objects.get()
    assert ticket.kind == FiledTicket.Kind.PROJECT_USERS
    assert ticket.project == project
    assert ticket.resolved_at is not None
    assert again == first
    assert snow.looked_up == []


def test_failing_to_record_doesnt_fail_the_task(pi, snow, monkeypatch, caplog):
    allocation, _ = make_storage_allocation(pi)

    def broken(**kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(FiledTicket.objects, "update_or_create", broken)
    with caplog.at_level(logging.ERROR, logger="ticketing.tasks"):
        incident = tasks.create_allocation_ticket(allocation.pk)

    assert incident.number == "INC0000001"
    assert "couldn't record it locally" in caplog.text


# -- Showing them --------------------------------------------------------------------------------

CARD = Template("{% include 'ticketing/filed_tickets.html' with subject=subject %}")


def render_card(user, subject, path="/"):
    """The ticket card for `subject` (an allocation or project), as `user` sees it on `path`."""
    request = RequestFactory().get(path)
    request.user = user
    return CARD.render(Context({"request": request, "subject": subject}))


@override_settings(SERVICENOW_URL=SNOW)
@pytest.mark.parametrize("who", ["admin", "ticketing_admin"])
def test_ticketing_admins_see_linked_tickets(pi, request, who):
    allocation, _ = make_storage_allocation(pi)
    file_ticket(f"coldfront:allocation:{allocation.pk}", allocation=allocation)

    html = render_card(request.getfixturevalue(who), allocation)

    assert "ServiceNow Tickets" in html
    assert f'href="{SNOW}/nav_to.do?uri=incident.do%3Fsys_id%3Dabc123"' in html
    assert ">INC0000009</a>" in html
    assert "Allocation request" in html


def test_others_see_nothing(pi):
    allocation, _ = make_storage_allocation(pi)
    file_ticket(f"coldfront:allocation:{allocation.pk}", allocation=allocation)

    assert render_card(pi, allocation).strip() == ""


@override_settings(SERVICENOW_URL=SNOW)
def test_each_ticket_names_and_links_what_its_for(pi, admin):
    allocation, _ = make_storage_allocation(pi, resource_name="RStore")
    file_ticket(f"coldfront:allocation:{allocation.pk}", allocation=allocation)

    html = render_card(admin, allocation)

    assert f'<a href="/allocation/{allocation.pk}/">RStore allocation (Active, ' in html


@override_settings(SERVICENOW_URL=SNOW)
def test_a_project_card_tells_its_allocations_apart(pi, admin):
    first, _ = make_storage_allocation(pi, resource_name="Research Storage")
    second, _ = make_storage_allocation(pi, resource_name="RStore")
    second.project = first.project
    second.save()
    file_ticket(f"coldfront:allocation:{first.pk}", allocation=first)
    file_ticket(f"coldfront:allocation:{second.pk}", number="INC0000010", allocation=second)

    html = render_card(admin, first.project)

    assert "Research Storage allocation (Active, " in html
    assert "RStore allocation (Active, " in html
    assert '<optgroup label="RStore allocation (Active, ' in html  # and both can be linked from here


@override_settings(SERVICENOW_URL=SNOW)
def test_a_storage_failure_shows_the_ticket_its_a_child_of(pi, admin):
    change = make_storage_change(pi)
    cid = f"coldfront:allocation-change:{change.pk}"
    file_ticket(cid, FiledTicket.Kind.ALLOCATION_CHANGE, allocation_change=change, allocation=change.allocation)
    file_ticket(
        f"{cid}:storage-failed",
        FiledTicket.Kind.STORAGE_FAILURE,
        number="INC0000010",
        allocation_change=change,
        allocation=change.allocation,
    )

    html = render_card(admin, change.allocation)

    assert 'child of <a href="' in html and ">INC0000009</a></small>" in html
    assert f'<a href="/allocation/change-request/{change.pk}/">Change request {change.pk} (' in html


@override_settings(SERVICENOW_URL=SNOW)
def test_a_storage_failure_without_a_request_ticket_says_so(pi, admin):
    allocation, _ = make_storage_allocation(pi)
    file_ticket(
        f"coldfront:allocation:{allocation.pk}:storage-failed", FiledTicket.Kind.STORAGE_FAILURE, allocation=allocation
    )

    assert "no request ticket to be a child of" in render_card(admin, allocation)


@override_settings(SERVICENOW_URL=SNOW)
def test_a_project_user_notice_shows_its_day(pi, admin):
    project = f.ProjectFactory(pi=pi, title="Genomics Lab")
    file_ticket(f"coldfront:project-users:{project.pk}:2026-10-10", FiledTicket.Kind.PROJECT_USERS, project=project)

    assert "Genomics Lab, 2026-10-10" in render_card(admin, project)


@override_settings(SERVICENOW_URL=SNOW)
def test_linked_tickets_are_marked_with_who_and_when(pi, admin):
    allocation, _ = make_storage_allocation(pi)
    ticket = file_ticket(f"coldfront:allocation:{allocation.pk}", allocation=allocation)
    ticket.source, ticket.linked_by = FiledTicket.Source.LINKED, admin
    ticket.linked_at = timezone.make_aware(datetime.datetime(2026, 10, 14, 9, 30))
    ticket.save()

    html = render_card(admin, allocation)

    assert 'title="Filed in ServiceNow; linked by admin01 on Oct. 14, 2026, 9:30 a.m.">linked</span>' in html


@override_settings(SERVICENOW_URL=SNOW)
def test_a_link_with_no_time_recorded_shows_without_one(pi, admin):
    allocation, _ = make_storage_allocation(pi)
    ticket = file_ticket(f"coldfront:allocation:{allocation.pk}", allocation=allocation)
    ticket.source, ticket.linked_by = FiledTicket.Source.LINKED, admin
    ticket.save()

    assert 'title="Filed in ServiceNow; linked by admin01">linked</span>' in render_card(admin, allocation)


@override_settings(SERVICENOW_URL=SNOW)
def test_admin_lists_them_read_only(client, admin, requestor):
    file_ticket("coldfront:hpc-account:jdoe01", FiledTicket.Kind.HPC_ACCOUNT, user=requestor)
    client.force_login(admin)

    listing = client.get(reverse("admin:ticketing_filedticket_changelist"))

    assert listing.status_code == 200
    assert b"INC0000009" in listing.content
    assert f'href="{SNOW}/nav_to.do'.encode() in listing.content
    assert client.get(reverse("admin:ticketing_filedticket_add")).status_code == 403
