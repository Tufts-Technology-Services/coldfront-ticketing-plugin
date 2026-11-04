"""Every ticket template renders cleanly, whatever it says.

A sweep over the files, so a new template is checked without a new test. It renders each one with
CONTEXT, which holds every variable any ticket template uses, and Django's string_if_invalid set to
a marker, so a misspelt or missing variable shows up instead of rendering as nothing. If a new
template needs a variable that isn't here, add it to CONTEXT.

The wording itself isn't tested anywhere: see rendering.py.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.test import override_settings

from ticketing.text import render_ticket_text

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates"
TICKET_TEMPLATES = sorted(
    path.relative_to(TEMPLATE_ROOT).as_posix() for path in (TEMPLATE_ROOT / "ticketing").rglob("*.txt")
)
MISSING = "MISSING-VARIABLE"

# Text with characters HTML-escaping would change, to catch a template rendered without autoescape off.
ESCAPABLE = "Smith & Jones's <lab>"


def make_person(username, full_name):
    return SimpleNamespace(username=username, get_full_name=lambda: full_name, email=f"{username}@example.edu")


CONTEXT = {
    "project": SimpleNamespace(title=ESCAPABLE),
    "allocation": SimpleNamespace(justification=ESCAPABLE, end_date="2027-06-30"),
    "change": SimpleNamespace(justification=ESCAPABLE, end_date_extension=90),
    "resource": SimpleNamespace(name="Tier 1 HPC", requires_payment=True),
    "pi": make_person("asmith02", "Alex Smith"),
    "requestor": make_person("jdoe01", "Jordan Doe"),
    "added_by": make_person("bjones03", "Bea Jones"),
    "user": make_person("jdoe01", "Jordan Doe"),
    "approver": "privera04",
    "parent": SimpleNamespace(number="INC0000001"),
    "url": "https://coldfront.example.edu/allocation/1/",
    "allocation_url": "https://coldfront.example.edu/allocation/1/",
    "path": "/cluster/tufts/lab",
    "windows_path": r"\\rstore.example.edu\lab$",
    "quota": "10 TB",
    "old_quota": "10 TB",
    "new_quota": "20 TB",
    "group": "lab",
    "admin_group": "labadmin",
    "job": "Share creation",
    "state": "failed: timed out",
    "reason": "permission denied",
    "details": ["Project:  lab"],
    "attributes": [{"name": "Core Usage (Hours)", "value": "1000"}],
    "attribute_changes": [
        {"name": "Core Usage (Hours)", "current": "1000", "requested": "2000", "old": "1000", "new": "2000"}
    ],
    "course_name": "BIO 101",
    "course_dir": "/cluster/tufts/bio101",
    "instructor_username": "asmith02",
    "student_username": "slee05",
    "ta_username": "bjones03",
}


@pytest.fixture(scope="module", autouse=True)
def missing_variables_show():
    engine = {**settings.TEMPLATES[0], "OPTIONS": {**settings.TEMPLATES[0]["OPTIONS"]}}
    engine["OPTIONS"]["string_if_invalid"] = f"{MISSING}(%s)"
    with override_settings(TEMPLATES=[engine, *settings.TEMPLATES[1:]]):
        yield


@pytest.fixture(scope="module", params=TICKET_TEMPLATES)
def template_name(request):
    return request.param


@pytest.fixture(scope="module")
def rendered_text(template_name, missing_variables_show):
    return render_ticket_text(template_name, CONTEXT)


def test_the_sweep_found_the_templates():
    assert "ticketing/allocation_request.txt" in TICKET_TEMPLATES
    assert "ticketing/partials/footer.txt" in TICKET_TEMPLATES


def test_renders_cleanly(rendered_text):
    assert MISSING not in rendered_text
    assert "{{" not in rendered_text and "{%" not in rendered_text and "{#" not in rendered_text
    assert "&amp;" not in rendered_text and "&#x27;" not in rendered_text and "&lt;" not in rendered_text


def test_has_no_runs_of_blank_lines(rendered_text):
    """A tag on a line of its own leaves the line behind; two blank lines in a row is the usual sign."""
    assert "\n\n\n" not in rendered_text


def test_turns_autoescape_off(template_name):
    """Tickets are plain text: escaping would put &amp; into them. Each file says so for itself,
    partials included, so one rendered on its own is plain too."""
    source = (TEMPLATE_ROOT / template_name).read_text()

    assert source.startswith("{% autoescape off %}")
    assert source.rstrip().endswith("{% endautoescape %}")


# Tufts' names for ColdFront's project roles: tickets say Owner, not PI, and Approver, not Manager.
COLDFRONT_ROLE_NAMES = re.compile(r"\bPI'?s?\b|\bManagers?\b|principal investigator", re.IGNORECASE)
RCRM_NAME = "Research Computing Resource Manager"  # a name, not a role


def test_names_roles_as_owner_and_approver(rendered_text):
    assert not COLDFRONT_ROLE_NAMES.findall(rendered_text.replace(RCRM_NAME, ""))
