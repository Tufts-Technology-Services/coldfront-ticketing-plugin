"""The pages in docs/servicenow-ticket-examples, kept current by running the real tasks.

Each page is hand-written prose (what the ticket is, when it's filed, who hears) above MARKER, and
below it what the plugin sends ServiceNow at each step of the ticket's life. That part is generated
here: each page's scenario runs the tasks, with Tufts' settings and made-up people, through the real
ServiceNowClient with its Table API calls answered from memory (RecordingServiceNow), so the pages
show the request bodies exactly as the plugin builds them. A page whose generated part no longer matches
fails its test; refresh the pages with REFRESH_COMMAND and review the diff.

Only the steps a scenario wraps in `snow.step(heading)` are shown; the rest is setup, such as opening
the request ticket that a page's resolve then resolves.
"""

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from coldfront.core.test_helpers import factories as f
from coldfront.core.utils import mail

from ticketing import client as servicenow
from ticketing import tasks
from ticketing.client import INCIDENT_TABLE, ServiceNowClient
from ticketing.tests.test_allocation_updates import add_group, add_member, add_project_key, add_windows_path
from ticketing.tickets import STATE_ACTIVE, STATE_CLOSED, STATE_RESOLVED

pytestmark = pytest.mark.django_db

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "docs" / "servicenow-ticket-examples"
REFRESH_COMMAND = "UPDATE_TICKET_EXAMPLES=1 uv run pytest ticketing/tests/test_ticket_examples.py"
MARKER = f"<!-- Generated below this line by ticketing/tests/test_ticket_examples.py. To refresh: {REFRESH_COMMAND} -->"

# Tufts' values for the settings the text depends on (coldfront-ansible's group_vars/tufts/main.yml).
SITE_NAME = "RCRM"
BASE_URL = "https://rcrm.it.tufts.edu"
SERVICE_ACCOUNT = "svc_rcrm"
RESOURCE_TEMPLATE_ALIASES = {
    "TTS Staff - Tier 1 HPC": "Tier 1 HPC",
    "Tier 1 Other": "Tier 1 HPC",
    "TTS Staff - Tier 1 RStore": "Tier 1 RStore",
    "Course - Tier 1 RStore": "Tier 1 RStore",
    "TTS Staff - Tier 2 Research": "Tier 2 Research",
}

STATE_NEW = "1"  # ServiceNow's own default; the plugin never sends it
STATE_LABELS = {STATE_NEW: "New", STATE_ACTIVE: "Active", STATE_RESOLVED: "Resolved", STATE_CLOSED: "Closed"}
# The fields a page's table shows, in order, and what it calls them. The text fields get blocks of
# their own; u_incident_type and correlation_id are the same on every ticket, so they're left out.
FIELD_LABELS = {
    "short_description": "Short description",
    "caller_id": "Caller (not emailed)",
    "u_contact_person": "Contact person (emailed)",
    "watch_list": "Watch list (emailed)",
    "contact_type": "Source (contact_type)",
    "u_business_service": "Business service",
    "u_action": "Action",
    "category": "Category",
    "subcategory": "Subcategory",
    "assignment_group": "Assignment group",
    "parent": "Parent incident",
    "assigned_to": "Assigned to",
    "state": "State",
    "close_code": "Close code",
    "close_notes": "Close notes",
}
# Shown for a ticket filed already resolved with no one assigned, where the row would otherwise be missing.
NO_ASSIGNEE = "no one"
OPENED_NEW = "Opened with no state, so ServiceNow starts it New in the group's queue."
OPENED_ACTIVE = "Opened with no state but an assignee, so ServiceNow starts it Active."
NOTHING_EMAILED = "Nothing customer-visible: no one is emailed."
DESCRIPTION = "**Description (internal, not emailed)**"
COMMENTS = "**Additional comments (customer-visible: ServiceNow emails the contact person and watch list)**"
WORK_NOTE = "**Work note (internal, not emailed)**"


class RecordingServiceNow(ServiceNowClient):
    """The real client, with the Table API answered from memory and each step's request recorded.

    Reference lookups answer with the value itself, so the pages name people and groups as ColdFront
    does rather than by sys_id.
    """

    def __init__(self):
        super().__init__("https://servicenow.invalid", "", "")
        self.incidents = {}  # sys_id -> the incident's fields, as stored
        self.sent = []  # (action, the body sent, the state the incident was left in), in order
        self.shown = []  # (heading, action, body, state)

    def create(self, table, fields, *, return_fields=("sys_id",)):
        number = len(self.incidents) + 1
        record = {"sys_id": f"sys-{number}", "number": f"INC{number:07d}", **fields}
        # What ServiceNow does with an incident sent without a state.
        record.setdefault("state", STATE_ACTIVE if fields.get("assigned_to") else STATE_NEW)
        self.incidents[record["sys_id"]] = record
        self.sent.append(("create", fields, record["state"]))
        return record

    def update(self, table, sys_id, fields, *, return_fields=("sys_id",)):
        record = self.incidents[sys_id]
        record.update(fields)
        self.sent.append(("update", fields, record["state"]))
        return record

    def find(self, table, column, value, *, return_fields=("sys_id",), limit=1):
        if table != INCIDENT_TABLE:
            return [{"sys_id": value}]
        return [record for record in self.incidents.values() if record.get(column) == value][:limit]

    @contextmanager
    def step(self, heading):
        """Show what the code in this block sends, which must be one create or update, under `heading`."""
        start = len(self.sent)
        yield
        sent = self.sent[start:]
        assert len(sent) == 1, f"{heading!r} sent {len(sent)} requests; a step shows exactly one"
        self.shown.append((heading, *sent[0]))


# -- Rendering ----------------------------------------------------------------------------------


def table_value(key, value):
    if value == SERVICE_ACCOUNT:
        # Name the setting, so a reader can tell the fallback from a person.
        return f"{value} (`TICKETING_ASSIGNED_TO`)"
    if key == "watch_list":
        value = value.replace(",", ", ")
    elif key == "state":
        value = f"{STATE_LABELS[value]} ({value})"
    return str(value).replace("|", "\\|")


def text_block(caption, text):
    # Some templates end with a newline; it means nothing to the reader, so the block ends at the text.
    return f"{caption}\n\n```text\n{text.rstrip(chr(10))}\n```"


def render_step(heading, action, sent, state):
    fields = dict(sent)
    parts = [f"### {heading}"]
    if action == "create" and "state" not in fields:
        fields["state"] = state
        parts.append(OPENED_ACTIVE if state == STATE_ACTIVE else OPENED_NEW)
    elif action == "create" and "assigned_to" not in fields:
        fields["assigned_to"] = NO_ASSIGNEE
    rows = [f"| {label} | {table_value(key, fields[key])} |" for key, label in FIELD_LABELS.items() if key in fields]
    if rows:
        parts.append("\n".join(["| Field | Value |", "|---|---|", *rows]))

    description, comments = fields.get("description"), fields.get("comments")
    if description and description == comments:
        emailed = "the contact person and watch list" if "watch_list" in fields else "the contact person"
        caption = f"**Description and additional comments (the same text; the comment is emailed to {emailed})**"
        parts.append(text_block(caption, description))
    else:
        if description:
            parts.append(text_block(DESCRIPTION, description))
        if comments:
            parts.append(text_block(COMMENTS, comments))
    if "work_notes" in fields:
        parts.append(text_block(WORK_NOTE, fields["work_notes"]))
    if action == "create" and not comments:
        parts.append(NOTHING_EMAILED)
    return "\n\n".join(parts)


def render_steps(shown):
    return "\n\n".join(render_step(*step) for step in shown) + "\n"


def prose_of(filename, page_text):
    """The hand-written part of a page: everything above the marker."""
    if MARKER not in page_text:
        pytest.fail(f"{filename} has no marker line; put this where the generated part starts:\n{MARKER}")
    return page_text[: page_text.index(MARKER)]


# -- The cast and their projects ------------------------------------------------------------------


class Cast:
    """The people in the examples (the README's "People in the examples")."""

    def __init__(self, in_request):
        self.in_request = in_request
        self.pi = make_person("asmith02", "Alex", "Smith")
        self.approver = make_person("bjones03", "Bea", "Jones")
        self.signup = make_person("jdoe01", "Jordan", "Doe")
        self.member = make_person("slee05", "Sam", "Lee")
        self.admin = make_person("privera04", "Pat", "Rivera", is_superuser=True)

    def project(self, title="Smith Lab", key="smithlab"):
        """Project 1, with its Project Key, and the PI on it as a Manager (as ColdFront adds them) and the Approver."""
        project = f.ProjectFactory(title=title, pi=self.pi)
        assert project.pk == 1, "the pages' links say project 1"
        add_project_key(project, key)
        for user in (self.pi, self.approver):
            add_member(project, user.username)
        return project

    def storage(
        self,
        resource_name,
        quota,
        *,
        project_title="Smith Lab",
        project_key="smithlab",
        path=None,
        windows_path=None,
        group=None,
        requires_payment=True,
        justification="Sequencing data for the 2026-27 NIH R01 project.",
    ):
        """Allocation 1, requested by the PI: New, with a public quota and whichever of the rest are given."""
        self.in_request(self.pi)
        resource = f.ResourceFactory(
            name=resource_name, resource_type=f.ResourceTypeFactory(name="Storage"), requires_payment=requires_payment
        )
        allocation = f.AllocationFactory(
            project=self.project(project_title, project_key),
            status=f.AllocationStatusChoiceFactory(name="New"),
            justification=justification,
        )
        assert allocation.pk == 1, "the pages' links say allocation 1"
        allocation.resources.add(resource)
        add_attribute(allocation, "Storage Quota (TB)", quota)
        if path:
            add_attribute(allocation, "Storage Path", path)
        if windows_path:
            add_windows_path(allocation, windows_path)
        if group:
            add_group(allocation, group)
        return allocation

    def activate(self, allocation):
        """The admin activates the allocation, as the view records it."""
        self.in_request(self.admin)
        allocation.status = f.AllocationStatusChoiceFactory(name="Active")
        allocation.save()

    def quota_change(self, allocation, new, justification="New imaging instrument doubles our data rate this year."):
        """Change request 1, the Approver asking for `new` TB. The quota still holds the current value."""
        self.in_request(self.approver)
        change = f.AllocationChangeRequestFactory(
            allocation=allocation,
            status=f.AllocationChangeStatusChoiceFactory(name="Pending"),
            justification=justification,
        )
        assert change.pk == 1, "the pages' links say change request 1"
        f.AllocationAttributeChangeRequestFactory(
            allocation_change_request=change,
            allocation_attribute=allocation.allocationattribute_set.get(
                allocation_attribute_type__name="Storage Quota (TB)"
            ),
            new_value=new,
        )
        return change

    def approve(self, change):
        """The admin approves the change, and the quota takes the new value, as approval does."""
        self.in_request(self.admin)
        change.status = f.AllocationChangeStatusChoiceFactory(name="Approved")
        change.save()
        for attribute_change in change.allocationattributechangerequest_set.all():
            attribute = attribute_change.allocation_attribute
            attribute.value = attribute_change.new_value
            attribute.save()


def make_person(username, first_name, last_name, **extra):
    return f.UserFactory(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=f"{first_name}.{last_name}@tufts.edu".lower(),
        **extra,
    )


def add_attribute(allocation, name, value):
    """A public attribute: customers see it."""
    f.AllocationAttributeFactory(
        allocation=allocation,
        allocation_attribute_type=f.AllocationAttributeTypeFactory(
            name=name, attribute_type=f.AAttributeTypeFactory(name="Text"), is_private=False
        ),
        value=value,
    )


# -- The pages ------------------------------------------------------------------------------------

HOME = "/cluster/home/jdoe01"
SETUP_FAILED = "Could not create home directory /cluster/home/jdoe01: permission denied"
HPC_PATH = "/cluster/tufts/smithlab"
RSTORE_PATH = "smb://rstore.it.tufts.edu/smithlab$"
RSTORE_WINDOWS_PATH = "\\\\rstore.it.tufts.edu\\smithlab$"

PAGES = {}


def example_page(filename):
    def register(scenario):
        PAGES[filename] = scenario
        return scenario

    return register


@example_page("01-hpc-account-created.md")
def hpc_account_created(snow, cast):
    with snow.step("Filed resolved"):
        tasks.resolve_hpc_account_ticket("jdoe01", HOME)


@example_page("02-hpc-account-request-waiting.md")
def hpc_account_request_waiting(snow, cast):
    with snow.step("Opened"):
        tasks.create_hpc_account_ticket("jdoe01")
    with snow.step("Resolved when the account is set up"):
        tasks.resolve_hpc_account_ticket("jdoe01", HOME)


@example_page("03-hpc-account-setup-failed.md")
def hpc_account_setup_failed(snow, cast):
    tasks.create_hpc_account_ticket("jdoe01")
    with snow.step("Opened"):
        tasks.file_hpc_account_failure_ticket("jdoe01", SETUP_FAILED)
    with snow.step("On a repeat failure"):
        tasks.file_hpc_account_failure_ticket("jdoe01", SETUP_FAILED)
    with snow.step("Resolved when a later attempt succeeds"):
        tasks.resolve_hpc_account_failure_ticket("jdoe01")


@example_page("04-allocation-request.md")
def allocation_request(snow, cast):
    # No path yet: the storage plugin sets it when it creates the share.
    allocation = cast.storage("Tier 1 HPC", "10")
    with snow.step("Opened"):
        tasks.create_allocation_ticket(allocation.pk, requestor_pk=cast.pi.pk)


def activated_request(cast, resource_name, quota, **storage):
    """Allocation 1: the PI's request, its ticket opened, then activated by the admin."""
    allocation = cast.storage(resource_name, quota, **storage)
    tasks.create_allocation_ticket(allocation.pk, requestor_pk=cast.pi.pk)
    cast.activate(allocation)
    return allocation


def new_allocation(snow, cast, resource_name, quota, **storage):
    """The PI's request activated and its share created: show the resolve."""
    allocation = activated_request(cast, resource_name, quota, **storage)
    with snow.step("Resolved"):
        tasks.resolve_allocation_ticket(allocation.pk, requestor_pk=cast.pi.pk)


@example_page("05-new-allocation-tier-1-hpc.md")
def new_tier_1_hpc(snow, cast):
    new_allocation(snow, cast, "Tier 1 HPC", "10", path=HPC_PATH)


@example_page("06-new-allocation-course-tier-1-hpc.md")
def new_course_tier_1_hpc(snow, cast):
    new_allocation(
        snow,
        cast,
        "Course - Tier 1 HPC",
        "1",
        project_title="BIO 101 Fall 2026",
        project_key="bio101_fall2026",
        path="/cluster/tufts/bio101",
        group="bio101",
        requires_payment=False,
    )


@example_page("07-new-allocation-tier-1-rstore.md")
def new_tier_1_rstore(snow, cast):
    new_allocation(snow, cast, "Tier 1 RStore", "5", path=RSTORE_PATH, windows_path=RSTORE_WINDOWS_PATH)


@example_page("08-new-allocation-tier-2-research.md")
def new_tier_2_research(snow, cast):
    new_allocation(snow, cast, "Tier 2 Research", "20")


@example_page("09-new-allocation-other-storage.md")
def new_other_storage(snow, cast):
    new_allocation(snow, cast, "Tier 3 Research", "50")


@example_page("10-quota-change-request.md")
def quota_change_request(snow, cast):
    allocation = cast.storage("Tier 1 HPC", "10", path=HPC_PATH)
    cast.activate(allocation)
    change = cast.quota_change(allocation, "20")
    with snow.step("Opened"):
        tasks.create_allocation_change_ticket(change.pk, requestor_pk=cast.approver.pk)


def approved_change(cast, resource_name, old, new, **storage):
    """Change request 1 on an active allocation: the Approver's, its ticket opened, then approved."""
    allocation = cast.storage(resource_name, old, **storage)
    cast.activate(allocation)
    change = cast.quota_change(allocation, new)
    tasks.create_allocation_change_ticket(change.pk, requestor_pk=cast.approver.pk)
    cast.approve(change)
    return allocation, change


def quota_change_made(snow, cast, resource_name, old, new, **storage):
    """The Approver's change request approved and applied: show the resolve."""
    _, change = approved_change(cast, resource_name, old, new, **storage)
    with snow.step("Resolved"):
        tasks.resolve_allocation_change_ticket(change.pk, requestor_pk=cast.approver.pk)


@example_page("11-quota-change-made-tier-1-hpc.md")
def quota_change_made_tier_1_hpc(snow, cast):
    quota_change_made(snow, cast, "Tier 1 HPC", "10", "20", path=HPC_PATH)


@example_page("12-quota-change-made-tier-1-rstore.md")
def quota_change_made_tier_1_rstore(snow, cast):
    quota_change_made(snow, cast, "Tier 1 RStore", "5", "8", path=RSTORE_PATH, windows_path=RSTORE_WINDOWS_PATH)


@example_page("13-storage-job-failed-new-share.md")
def storage_job_failed_new_share(snow, cast):
    allocation = activated_request(cast, "Tier 1 HPC", "10", path=HPC_PATH)
    state = f"failed: VAST API timed out creating {HPC_PATH}"
    with snow.step("Opened"):
        tasks.file_storage_failure_ticket(allocation.pk, state=state)
    with snow.step("On a repeat failure"):
        tasks.file_storage_failure_ticket(allocation.pk, state=state)
    with snow.step("Resolved when the job succeeds"):
        tasks.resolve_storage_failure_ticket(allocation.pk)


@example_page("14-storage-job-failed-quota-change.md")
def storage_job_failed_quota_change(snow, cast):
    allocation, change = approved_change(
        cast, "Tier 1 RStore", "5", "8", path=RSTORE_PATH, windows_path=RSTORE_WINDOWS_PATH
    )
    with snow.step("Opened"):
        tasks.file_storage_failure_ticket(allocation.pk, change.pk, state="failed: quota below current usage")


@example_page("15-project-users-changed.md")
def project_users_changed(snow, cast):
    project = cast.project()
    with snow.step("Filed resolved"):
        tasks.create_project_users_ticket(project.pk, requestor_pk=cast.approver.pk)


@example_page("16-added-to-project.md")
def added_to_project(snow, cast):
    project = cast.project()
    add_member(project, cast.member.username, role="User")
    with snow.step("Filed resolved"):
        tasks.create_project_user_added_ticket(project.pk, cast.member.pk, requestor_pk=cast.approver.pk)


# -- The tests ------------------------------------------------------------------------------------


@pytest.fixture
def tufts(settings, monkeypatch):
    settings.TICKETING_SITE_NAME = SITE_NAME
    settings.TICKETING_ASSIGNED_TO = SERVICE_ACCOUNT
    settings.TICKETING_RESOURCE_TEMPLATE_ALIASES = RESOURCE_TEMPLATE_ALIASES
    # build_link reads the base URL coldfront bound at import, not the live setting.
    monkeypatch.setattr(mail, "CENTER_BASE_URL", BASE_URL)


def generate_example_page(filename, monkeypatch, in_request):
    snow = RecordingServiceNow()
    monkeypatch.setattr(servicenow, "client_from_settings", lambda: snow)
    PAGES[filename](snow, Cast(in_request))
    return render_steps(snow.shown)


@pytest.mark.parametrize("filename", PAGES)
def test_the_example_page_is_current(filename, tufts, monkeypatch, in_request):
    path = EXAMPLES_DIR / filename
    if not EXAMPLES_DIR.is_dir():
        pytest.skip("the docs aren't here (an installed package)")

    updating = bool(os.environ.get("UPDATE_TICKET_EXAMPLES"))
    if not path.exists():
        if not updating:
            pytest.fail(f"{filename} doesn't exist yet; create it with: {REFRESH_COMMAND}")
        page_text = f"# {filename}\n\nWhen: (write what files this ticket, and who hears)\n\n{MARKER}"
    else:
        page_text = path.read_text()
    expected = f"{prose_of(filename, page_text)}{MARKER}\n\n{generate_example_page(filename, monkeypatch, in_request)}"

    if updating:
        path.write_text(expected)
        return
    assert page_text == expected, f"{filename} is out of date with what the plugin sends; run: {REFRESH_COMMAND}"


def test_every_example_page_has_a_scenario():
    if not EXAMPLES_DIR.is_dir():
        pytest.skip("the docs aren't here (an installed package)")
    pages = {path.name for path in EXAMPLES_DIR.glob("*.md")} - {"README.md"}

    assert pages == set(PAGES)
