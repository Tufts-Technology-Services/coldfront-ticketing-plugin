"""ServiceNowIncident dataclass and its conversion to a ServiceNow incident payload.

Adapted from rt-ticket-utils, where the dataclass is `Ticket`; the verbatim original is in
docs/reference/ and docs/plan.md explains what changed and why. Pure stdlib -- no Django or ColdFront imports, so it is testable standalone.
"""

import logging
import re
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

# Column limits on the Tufts instance (/api/now/ui/meta/incident).
SHORT_DESCRIPTION_MAX_LENGTH = 160
LONG_TEXT_MAX_LENGTH = 4000
# Multi-line text columns that share the 4000-char limit.
LONG_TEXT_FIELDS = ("description", "close_notes", "comments", "work_notes")
TRUNCATION_MARKER = "\n[truncated]"
DEFAULT_BUSINESS_SERVICE = "High Performance Computing (HPC)"
# u_action choice values: a new thing set up, or a change to an existing one.
U_ACTION_ACTIVATE = "Activate/Setup/Install"
U_ACTION_MOVE_ADD_CHANGE = "Move/Add/Change"

_WHITESPACE = re.compile(r"\s+")

# Reference fields, and the (table, column) that identifies the record each one points at. A ServiceNowIncident
# holds these in ColdFront's terms -- UTLNs, group names, incident numbers -- but the Table API
# wants sys_ids, so to_payload() resolves them. UTLN == sys_user.user_name.
#
# Verified against the Tufts dev instance (2026-09-25) by reading existing TTS Research Technology
# incidents: all of these hold sys_ids, u_contact_person's resolve to sys_user records, and the
# group and "High Performance Computing (HPC)" sys_ids match what these lookups return.
REFERENCE_FIELDS = {
    "u_contact_person": ("sys_user", "user_name"),
    "caller_id": ("sys_user", "user_name"),
    "assigned_to": ("sys_user", "user_name"),
    "watch_list": ("sys_user", "user_name"),
    "assignment_group": ("sys_user_group", "name"),
    "u_business_service": ("cmdb_ci_service", "name"),
    # The ticket this is a child of. Tufts' incident table has no parent_incident (checked on the
    # dev instance, 2026-10-15); parent, a reference to task, is the one there is.
    "parent": ("task", "number"),
}


@dataclass(kw_only=True)
class ServiceNowIncident:
    """An incident, in ColdFront's terms.

    state/close_code/close_notes are resolution fields: leave them unset to open an incident, set
    them on the update that resolves it. Unset, the instance picks the opening state: New (1) when
    assigned_to is unset too, as for requests, and Active (2) when it is set (both seen on the dev
    instance).

    Choice fields are sent verbatim, so they must be stored values, not UI labels. Verified against
    the dev instance's field metadata (/api/now/ui/meta/incident): the string defaults below
    (u_incident_type, category, subcategory, u_action) are valid values identical to their labels;
    subcategory "Research Systems" is only valid under category "Research". state stores integers
    (1 New, 2 Active, 8 Pending Request, 6 Resolved, 7 Closed, ...). close_code values differ from
    their labels -- e.g. "Solved Remotely (Permanently)" is labelled "Solved Remotely" -- and the
    vendored "Resolved Remotely" is NOT a valid value.

    Declared in the original library's field order, with API-only fields after it; kw_only makes
    that possible despite required fields being interleaved with defaulted ones.
    """

    u_incident_type: str = "Request"
    u_contact_person: str
    caller_id: str
    # "Source" in the UI. The email intake took this as `source`, which is not an incident column
    # (confirmed on the dev instance) -- the Table API would silently drop it. Unset lets ServiceNow apply
    # its default; emailed tickets carry "email".
    contact_type: str | None = None
    category: str = "Research"
    subcategory: str = "Research Systems"
    u_business_service: str = DEFAULT_BUSINESS_SERVICE
    short_description: str
    # Unset, the incident waits unassigned in assignment_group's queue. Required on a ticket filed
    # already resolved (see __post_init__).
    assigned_to: str | None = None
    assignment_group: str = "TTS Research Technology"
    parent: str | None = None
    watch_list: list[str] | None = None
    u_action: str = U_ACTION_ACTIVATE
    state: str | None = None
    close_code: str | None = None
    close_notes: str | None = None

    description: str | None = None
    # Deterministic per ColdFront object, so a retried task can find the incident it already made.
    correlation_id: str | None = None
    comments: str | None = None  # customer-visible journal entry
    work_notes: str | None = None  # internal journal entry

    def __post_init__(self):
        if self.state is not None and not self.assigned_to:
            raise ValueError(f"A ticket filed with state {self.state!r} needs assigned_to")


def clean_short_description(value: str) -> str:
    """Collapse whitespace and truncate: short_description is a single-line, length-capped column.

    Values here include user-supplied text such as project titles.
    """
    return _WHITESPACE.sub(" ", value).strip()[:SHORT_DESCRIPTION_MAX_LENGTH]


def clean_long_text(value: str) -> str:
    """Fit multi-line text into its column, marking the cut so readers know text is missing.

    Line breaks are kept. Rendered allocation detail can run long, and ServiceNow would otherwise
    truncate silently.
    """
    if len(value) <= LONG_TEXT_MAX_LENGTH:
        return value
    return value[: LONG_TEXT_MAX_LENGTH - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def fit_update(fields: dict) -> dict:
    """Apply the long-text limit to a partial-update dict, e.g. the fields that resolve an incident."""
    return {key: clean_long_text(value) if key in LONG_TEXT_FIELDS else value for key, value in fields.items()}


def to_update(fields: dict, resolve) -> dict:
    """Build the JSON body for a partial update: references resolved as in to_payload, long text fitted."""
    body = fit_update(fields)
    for key, value in body.items():
        if key in REFERENCE_FIELDS:
            table, column = REFERENCE_FIELDS[key]
            if isinstance(value, list):
                body[key] = ",".join(_resolve_members(key, table, column, value, resolve))
            else:
                body[key] = resolve(table, column, value)
    return body


def to_payload(incident: ServiceNowIncident, resolve) -> dict:
    """Build the JSON body that creates an incident.

    `resolve(table, column, value)` returns the sys_id of the record where `column == value`; it is
    called once per reference value (see REFERENCE_FIELDS). Unset fields and empty lists are
    omitted rather than sent as null, which ServiceNow would store as a blank.

    A list member `resolve` can't find (it raises LookupError) is logged and left off, so one
    unknown watcher doesn't stop the ticket; any other unknown reference raises.

    Updates (e.g. resolving) carry only a few fields, so they take a plain dict rather than a ServiceNowIncident.
    """
    payload = {}
    for key, value in asdict(incident).items():
        if value is None or value == []:
            continue
        if key in REFERENCE_FIELDS:
            table, column = REFERENCE_FIELDS[key]
            if isinstance(value, list):
                value = ",".join(_resolve_members(key, table, column, value, resolve))
                if not value:
                    continue
            else:
                value = resolve(table, column, value)
        elif key == "short_description":
            value = clean_short_description(value)
        elif key in LONG_TEXT_FIELDS:
            value = clean_long_text(value)
        payload[key] = value
    return payload


def _resolve_members(key, table, column, values, resolve):
    """The sys_ids of the `values` that resolve, skipping (and logging) any that don't."""
    sys_ids = []
    for value in values:
        try:
            sys_ids.append(resolve(table, column, value))
        except LookupError as e:
            logger.warning("Leaving %r off %s: %s", value, key, e)
    return sys_ids
