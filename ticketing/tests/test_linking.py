import datetime

import pytest
from coldfront.core.test_helpers import factories as f
from django.contrib.messages import get_messages
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse

from ticketing import linking, tasks
from ticketing.client import Incident, ServiceNowError
from ticketing.models import FiledTicket
from ticketing.tests.test_allocation_updates import make_storage_allocation, make_storage_change

pytestmark = pytest.mark.django_db


class FakeServiceNow:
    """Incidents as mutable records, findable by number or correlation_id, as the linking needs."""

    def __init__(self):
        self.records = []
        self.updates = []

    def add(self, *, state="1", correlation_id="", assigned_to=None, **fields):
        n = len(self.records) + 1
        record = {
            "sys_id": f"i-{n}",
            "number": f"INC{n:07d}",
            "state": state,
            "correlation_id": correlation_id,
            "assigned_to": assigned_to,
            **fields,
        }
        self.records.append(record)
        return record

    def _incident(self, record):
        return Incident(
            sys_id=record["sys_id"],
            number=record["number"],
            state=record["state"],
            assigned_to=record["assigned_to"],
            correlation_id=record["correlation_id"] or None,
        )

    def find_incident(self, correlation_id):
        matches = [r for r in self.records if r["correlation_id"] == correlation_id]
        return self._incident(matches[0]) if matches else None

    def find_incident_by_number(self, number):
        matches = [r for r in self.records if r["number"] == number]
        return self._incident(matches[0]) if matches else None

    def create_incident(self, ticket):
        fields = {k: v for k, v in vars(ticket).items() if k not in ("state", "correlation_id", "assigned_to")}
        record = self.add(
            state=ticket.state or "1", correlation_id=ticket.correlation_id, assigned_to=ticket.assigned_to, **fields
        )
        return self._incident(record)

    def update_incident(self, sys_id, fields):
        self.updates.append((sys_id, fields))
        next(r for r in self.records if r["sys_id"] == sys_id).update(fields)

    def by_number(self, number):
        return next(r for r in self.records if r["number"] == number)


@pytest.fixture
def snow(monkeypatch):
    client = FakeServiceNow()
    monkeypatch.setattr(tasks, "client_from_settings", lambda: client)
    monkeypatch.setattr(linking, "client_from_settings", lambda: client)
    return client


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(tasks.timezone, "localdate", lambda: datetime.date(2026, 9, 28))
    monkeypatch.setattr(linking.timezone, "localdate", lambda: datetime.date(2026, 9, 28))


def allocation_target(allocation):
    return linking.link_targets(allocation)[0]


# -- What can be linked --------------------------------------------------------------------------


def test_an_allocation_offers_its_request_then_its_change_requests_newest_first(pi):
    change = make_storage_change(pi)
    allocation = change.allocation
    newer = f.AllocationChangeRequestFactory(allocation=allocation)

    targets = linking.link_targets(allocation)

    assert [t.correlation_id for t in targets] == [
        f"coldfront:allocation:{allocation.pk}",
        f"coldfront:allocation-change:{newer.pk}",
        f"coldfront:allocation-change:{change.pk}",
    ]
    assert targets[0].label == "The allocation request"
    assert targets[1].label.startswith(f"Change request {newer.pk} (")
    assert targets[1].subjects == {"allocation_change": newer, "allocation": allocation}


def test_a_project_offers_todays_user_changes(pi, today):
    project = f.ProjectFactory(pi=pi)

    [target] = linking.link_targets(project)

    assert target.correlation_id == f"coldfront:project-users:{project.pk}:2026-09-28"
    assert target.label == "Today's user changes (2026-09-28)"


# -- Linking -------------------------------------------------------------------------------------


def test_linking_adopts_the_incident(pi, admin, snow):
    allocation, _ = make_storage_allocation(pi)
    theirs = snow.add()

    linked = linking.link_ticket(" inc0000001 ", allocation_target(allocation), admin)

    assert linked.incident.number == "INC0000001" and linked.duplicate is None
    assert theirs["correlation_id"] == f"coldfront:allocation:{allocation.pk}"
    ticket = FiledTicket.objects.get()
    assert ticket.number == "INC0000001" and ticket.sys_id == theirs["sys_id"]
    assert ticket.source == FiledTicket.Source.LINKED
    assert ticket.linked_by == admin
    assert ticket.allocation == allocation
    assert ticket.kind == FiledTicket.Kind.ALLOCATION


def test_coldfronts_own_incident_is_closed_as_a_duplicate(pi, admin, snow):
    allocation, _ = make_storage_allocation(pi)
    ours = tasks.create_allocation_ticket(allocation.pk)
    theirs = snow.add()

    linked = linking.link_ticket(theirs["number"], allocation_target(allocation), admin)

    assert linked.duplicate == ours
    closed = snow.by_number(ours.number)
    assert closed["state"] == "7"
    assert closed["u_duplicate_of"] == theirs["sys_id"]
    assert closed["close_notes"] == "Duplicate ticket"
    assert closed["correlation_id"] == ""
    assert closed["assigned_to"] == "svc_test"  # it was unassigned
    [closing] = [fields for sys_id, fields in snow.updates if sys_id == ours.sys_id]
    assert "close_code" not in closing  # Tufts closes duplicates without one
    assert theirs["correlation_id"] == f"coldfront:allocation:{allocation.pk}"
    assert FiledTicket.objects.get().number == theirs["number"]  # the record now points at theirs


def test_the_duplicates_correlation_id_is_cleared_before_the_adopted_one_is_set(pi, admin, snow):
    allocation, _ = make_storage_allocation(pi)
    ours = tasks.create_allocation_ticket(allocation.pk)
    theirs = snow.add()

    linking.link_ticket(theirs["number"], allocation_target(allocation), admin)

    assert [(sys_id, fields["correlation_id"]) for sys_id, fields in snow.updates] == [
        (ours.sys_id, ""),
        (theirs["sys_id"], f"coldfront:allocation:{allocation.pk}"),
    ]


def test_a_duplicate_someone_picked_up_keeps_its_assignee(pi, admin, snow):
    allocation, _ = make_storage_allocation(pi)
    ours = snow.add(state="2", correlation_id=f"coldfront:allocation:{allocation.pk}", assigned_to="u-admin")
    theirs = snow.add()

    linking.link_ticket(theirs["number"], allocation_target(allocation), admin)

    assert ours["assigned_to"] == "u-admin"
    assert ours["state"] == "7"


def test_linking_again_changes_nothing(pi, admin, snow):
    allocation, _ = make_storage_allocation(pi)
    theirs = snow.add()
    linking.link_ticket(theirs["number"], allocation_target(allocation), admin)
    snow.updates.clear()

    linked = linking.link_ticket(theirs["number"], allocation_target(allocation), admin)

    assert linked.duplicate is None
    assert snow.updates == []
    assert FiledTicket.objects.count() == 1


@pytest.mark.parametrize(
    "number, setup, error",
    [
        ("12345", None, "isn't an incident number"),
        ("INC9999999", None, "There's no incident INC9999999"),
        ("INC0000001", {"correlation_id": "coldfront:allocation:999"}, "already linked to something else"),
        ("INC0000001", {"state": "6"}, "already resolved"),
        ("INC0000001", {"state": "7"}, "already resolved"),
    ],
)
def test_refusals(pi, admin, snow, number, setup, error):
    allocation, _ = make_storage_allocation(pi)
    snow.add(**(setup or {}))

    with pytest.raises(linking.LinkError, match=error):
        linking.link_ticket(number, allocation_target(allocation), admin)

    assert snow.updates == []
    assert not FiledTicket.objects.exists()


def test_refuses_when_coldfronts_incident_is_already_resolved(pi, admin, snow):
    allocation, _ = make_storage_allocation(pi)
    ours = snow.add(state="6", correlation_id=f"coldfront:allocation:{allocation.pk}")
    theirs = snow.add()

    with pytest.raises(linking.LinkError, match=f"ColdFront's {ours['number']} for this is already resolved"):
        linking.link_ticket(theirs["number"], allocation_target(allocation), admin)

    assert snow.updates == []


# -- The tasks then treat it as their own --------------------------------------------------------


def test_a_linked_allocation_request_is_resolved_not_refiled(pi, admin, snow):
    allocation, _ = make_storage_allocation(pi)
    theirs = snow.add()
    linking.link_ticket(theirs["number"], allocation_target(allocation), admin)

    assert tasks.create_allocation_ticket(allocation.pk).number == theirs["number"]
    tasks.resolve_allocation_ticket(allocation.pk)

    assert len(snow.records) == 1
    assert theirs["state"] == "6"
    assert theirs["comments"].startswith("Hello,")
    ticket = FiledTicket.objects.get()
    assert ticket.resolved_at is not None
    assert ticket.source == FiledTicket.Source.LINKED  # resolving keeps how it came


def test_a_linked_change_request_is_resolved(pi, admin, snow):
    change = make_storage_change(pi)
    theirs = snow.add()
    target = next(t for t in linking.link_targets(change.allocation) if t.kind == FiledTicket.Kind.ALLOCATION_CHANGE)
    linking.link_ticket(theirs["number"], target, admin)

    tasks.resolve_allocation_change_ticket(change.pk)

    assert theirs["state"] == "6"
    assert FiledTicket.objects.get().allocation_change == change


def test_todays_project_user_changes_resolve_the_linked_ticket(pi, admin, snow, today):
    project = f.ProjectFactory(pi=pi, title="Genomics Lab")
    theirs = snow.add()
    [target] = linking.link_targets(project)
    linking.link_ticket(theirs["number"], target, admin)

    tasks.create_project_users_ticket(project.pk)

    assert len(snow.records) == 1
    assert theirs["state"] == "6"
    assert 'The users on your project "Genomics Lab"' in theirs["comments"]
    assert FiledTicket.objects.get().resolved_at is not None


def test_a_project_user_notice_already_filed_today_isnt_looked_up_again(pi, snow, today):
    project = f.ProjectFactory(pi=pi)
    tasks.create_project_users_ticket(project.pk)
    snow.updates.clear()
    find = snow.find_incident
    snow.find_incident = lambda cid: pytest.fail(f"looked up {cid}")

    tasks.create_project_users_ticket(project.pk)

    snow.find_incident = find
    assert len(snow.records) == 1 and snow.updates == []


# -- The form ------------------------------------------------------------------------------------


def link_url(subject_type, pk):
    return reverse("ticketing-link", kwargs={"subject_type": subject_type, "pk": pk})


def flashed(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def test_posting_links_and_returns_to_the_allocation(pi, admin, client, snow):
    allocation, _ = make_storage_allocation(pi)
    ours = tasks.create_allocation_ticket(allocation.pk)
    theirs = snow.add()
    client.force_login(admin)

    response = client.post(
        link_url("allocation", allocation.pk),
        {"number": theirs["number"], "target": f"coldfront:allocation:{allocation.pk}"},
    )

    assert response.status_code == 302
    assert response.url == reverse("allocation-detail", kwargs={"pk": allocation.pk})
    assert flashed(response) == [
        f"Linked {theirs['number']} to the allocation request. "
        f"ColdFront's {ours.number} was closed as a duplicate of it."
    ]


def test_a_refusal_is_shown_on_the_page(pi, admin, client, snow):
    project = f.ProjectFactory(pi=pi)
    client.force_login(admin)

    response = client.post(
        link_url("project", project.pk),
        {"number": "INC9999999", "target": linking.link_targets(project)[0].correlation_id},
    )

    assert response.url == reverse("project-detail", kwargs={"pk": project.pk})
    assert flashed(response) == ["Not linked: There's no incident INC9999999 in ServiceNow"]


def test_servicenow_failing_is_shown_and_logged(pi, admin, client, snow, monkeypatch, caplog):
    allocation, _ = make_storage_allocation(pi)
    snow.add()

    def down(number):
        raise ServiceNowError("GET -> 503")

    monkeypatch.setattr(snow, "find_incident_by_number", down)
    client.force_login(admin)

    response = client.post(
        link_url("allocation", allocation.pk),
        {"number": "INC0000001", "target": f"coldfront:allocation:{allocation.pk}"},
    )

    assert flashed(response) == ["Not linked: ServiceNow couldn't be reached or refused the change. Try again."]
    assert "Linking 'INC0000001'" in caplog.text


def test_a_target_from_elsewhere_is_refused(pi, admin, client, snow):
    allocation, _ = make_storage_allocation(pi)
    client.force_login(admin)

    response = client.post(
        link_url("allocation", allocation.pk), {"number": "INC0000001", "target": "coldfront:allocation:999"}
    )

    assert flashed(response) == ["Choose what the ServiceNow ticket is for."]
    assert snow.updates == []


def test_linking_is_superusers_only(pi, client, snow):
    allocation, _ = make_storage_allocation(pi)
    client.force_login(pi)

    response = client.post(
        link_url("allocation", allocation.pk),
        {"number": "INC0000001", "target": f"coldfront:allocation:{allocation.pk}"},
    )

    assert response.status_code == 403


def test_unknown_subjects_are_not_found(admin, client):
    client.force_login(admin)

    assert client.post(link_url("resource", 1), {}).status_code == 404


def test_the_card_offers_the_form_even_with_no_tickets(pi, admin):
    allocation, _ = make_storage_allocation(pi)
    request = RequestFactory().get("/")
    request.user = admin

    html = Template("{% include 'ticketing/filed_tickets.html' with subject=allocation %}").render(
        Context({"request": request, "allocation": allocation})
    )

    assert "No ServiceNow tickets yet." in html
    assert f'action="{link_url("allocation", allocation.pk)}"' in html
    assert f'<option value="coldfront:allocation:{allocation.pk}">The allocation request</option>' in html
