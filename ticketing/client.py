"""Thin client for the ServiceNow Table API.

No Django imports at module level, so it is testable standalone; client_from_settings() reads the
Django settings when a task needs a configured client.
"""

from dataclasses import dataclass, field

import requests

from ticketing.tickets import Ticket, to_payload, to_update

DEFAULT_TIMEOUT = 30
INCIDENT_TABLE = "incident"
# Pinned to a version so an instance upgrade can't change the API's behavior underneath us. Matches
# the existing Tufts client in rt-analytics.
TABLE_API_PATH = "/api/now/v1/table"


class ServiceNowError(Exception):
    """The API rejected a request, or a lookup didn't match exactly one record."""


class UnknownReference(ServiceNowError, LookupError):
    """A lookup didn't match exactly one record. to_payload drops these from list fields."""


@dataclass(frozen=True)
class Incident:
    sys_id: str
    number: str
    # Only filled in by find_incident. Left out of equality: an Incident is identified by sys_id.
    state: str | None = field(default=None, compare=False)
    assigned_to: str | None = field(default=None, compare=False)  # a sys_id; None if unassigned
    correlation_id: str | None = field(default=None, compare=False)  # only from find_incident_by_number


class ServiceNowClient:
    def __init__(self, base_url, username, password, *, timeout=DEFAULT_TIMEOUT, session=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        # Reference lookups repeat within a task (caller_id and u_contact_person are usually the
        # same person) and the targets -- users, groups -- don't change mid-task.
        self._sys_ids = {}

    # -- Table API primitives ------------------------------------------------------------------

    def _request(self, method, table, sys_id=None, *, empty_on_404=False, **kwargs):
        url = f"{self.base_url}{TABLE_API_PATH}/{table}"
        if sys_id:
            url = f"{url}/{sys_id}"
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        # A query matching nothing comes back as 404 "No Record found", not 200 with an empty list.
        # Rows hidden by ACLs look the same, so an account that can't read a table sees it as empty.
        if empty_on_404 and response.status_code == 404:
            return []
        if not response.ok:
            raise ServiceNowError(f"{method} {url} -> {response.status_code}: {response.text[:500]}")
        return response.json()["result"]

    def create(self, table, fields, *, return_fields=("sys_id",)):
        params = {"sysparm_fields": ",".join(return_fields), "sysparm_exclude_reference_link": "true"}
        return self._request("POST", table, json=fields, params=params)

    def update(self, table, sys_id, fields, *, return_fields=("sys_id",)):
        params = {"sysparm_fields": ",".join(return_fields), "sysparm_exclude_reference_link": "true"}
        return self._request("PATCH", table, sys_id, json=fields, params=params)

    def find(self, table, column, value, *, return_fields=("sys_id",), limit=1):
        """Records where `column == value`. One condition only, so the value can't alter the query."""
        # `^` joins encoded-query conditions (`^OR...`, `^NQ...`); a value containing one would
        # smuggle in extra conditions.
        if "^" in str(value):
            raise ValueError(f"Refusing to query {table}.{column} with a value containing '^': {value!r}")
        params = {
            "sysparm_query": f"{column}={value}",
            "sysparm_fields": ",".join(return_fields),
            "sysparm_limit": str(limit),
            "sysparm_exclude_reference_link": "true",
        }
        return self._request("GET", table, params=params, empty_on_404=True)

    def sys_id_for(self, table, column, value):
        """The sys_id of the single record where `column == value`; raises if none or several."""
        key = (table, column, value)
        if key not in self._sys_ids:
            matches = self.find(table, column, value, limit=2)
            if len(matches) != 1:
                found = "no" if not matches else "more than one"
                raise UnknownReference(f"Expected one {table} record with {column}={value!r}, found {found}")
            self._sys_ids[key] = matches[0]["sys_id"]
        return self._sys_ids[key]

    # -- Incidents -----------------------------------------------------------------------------

    def create_incident(self, ticket: Ticket) -> Incident:
        payload = to_payload(ticket, self.sys_id_for)
        result = self.create(INCIDENT_TABLE, payload, return_fields=("sys_id", "number"))
        return Incident(sys_id=result["sys_id"], number=result["number"])

    def find_incident(self, correlation_id) -> Incident | None:
        return self._find_incident("correlation_id", correlation_id)

    def find_incident_by_number(self, number) -> Incident | None:
        """The incident numbered `number` (e.g. INC0012345), with its correlation_id."""
        return self._find_incident("number", number, "correlation_id")

    def _find_incident(self, column, value, *extra_fields) -> Incident | None:
        fields = ("sys_id", "number", "state", "assigned_to", *extra_fields)
        matches = self.find(INCIDENT_TABLE, column, value, return_fields=fields)
        if not matches:
            return None
        match = matches[0]
        return Incident(
            sys_id=match["sys_id"],
            number=match["number"],
            state=match.get("state"),
            # An empty reference or string comes back as "".
            assigned_to=match.get("assigned_to") or None,
            correlation_id=match.get("correlation_id") or None,
        )

    def update_incident(self, sys_id, fields: dict) -> None:
        """Apply a partial update. Leave keys out rather than passing None; long text is truncated,
        and references (e.g. assigned_to) are given in ColdFront's terms and resolved as on create."""
        self.update(INCIDENT_TABLE, sys_id, to_update(fields, self.sys_id_for))


def client_from_settings() -> ServiceNowClient:
    """A client configured from SERVICENOW_URL / SERVICENOW_USERNAME / SERVICENOW_PASSWORD."""
    from django.conf import settings

    return ServiceNowClient(
        settings.SERVICENOW_URL,
        settings.SERVICENOW_USERNAME,
        settings.SERVICENOW_PASSWORD,
        timeout=getattr(settings, "SERVICENOW_TIMEOUT", DEFAULT_TIMEOUT),
    )
