import json

import pytest
import responses
from responses import matchers

from ticketing.client import Incident, ServiceNowClient, ServiceNowError, UnknownReference
from ticketing.tickets import Ticket

BASE = "https://servicenow.example"
TABLE = f"{BASE}/api/now/v1/table"


@pytest.fixture
def client():
    return ServiceNowClient(BASE + "/", "svc_user", "secret")


@pytest.fixture
def api():
    with responses.RequestsMock() as rsps:
        yield rsps


def user_lookup(api, utln, sys_id):
    api.get(
        f"{TABLE}/sys_user",
        match=[matchers.query_param_matcher({"sysparm_query": f"user_name={utln}"}, strict_match=False)],
        json={"result": [{"sys_id": sys_id}]},
    )


def ticket():
    return Ticket(
        u_contact_person="jdoe01",
        caller_id="jdoe01",
        short_description="New allocation request",
        assigned_to="svc_test",
        correlation_id="coldfront:allocation:7",
    )


def test_create_incident_resolves_references_and_returns_number(client, api):
    user_lookup(api, "jdoe01", "u-jdoe")
    user_lookup(api, "svc_test", "u-svc")
    api.get(f"{TABLE}/sys_user_group", json={"result": [{"sys_id": "g-rt"}]})
    api.get(f"{TABLE}/cmdb_ci_service", json={"result": [{"sys_id": "s-hpc"}]})
    create = api.post(
        f"{TABLE}/incident",
        match=[matchers.query_param_matcher({"sysparm_fields": "sys_id,number"}, strict_match=False)],
        json={"result": {"sys_id": "i-1", "number": "INC0000001"}},
        status=201,
    )

    incident = client.create_incident(ticket())

    assert incident == Incident(sys_id="i-1", number="INC0000001")
    body = json.loads(create.calls[0].request.body)
    assert body["caller_id"] == body["u_contact_person"] == "u-jdoe"
    assert body["assigned_to"] == "u-svc"
    assert body["assignment_group"] == "g-rt"
    assert body["u_business_service"] == "s-hpc"
    assert body["correlation_id"] == "coldfront:allocation:7"
    assert "state" not in body


def test_sends_basic_auth_and_json_headers(client, api):
    call = api.get(f"{TABLE}/incident", json={"result": []})

    client.find_incident("coldfront:allocation:7")

    headers = call.calls[0].request.headers
    assert headers["Authorization"].startswith("Basic ")
    assert headers["Accept"] == "application/json"


def test_repeated_lookups_hit_the_api_once(client, api):
    lookup = api.get(f"{TABLE}/sys_user", json={"result": [{"sys_id": "u-jdoe"}]})

    client.sys_id_for("sys_user", "user_name", "jdoe01")
    client.sys_id_for("sys_user", "user_name", "jdoe01")

    assert lookup.call_count == 1


NO_RECORD = {
    "error": {
        "message": "No Record found",
        "detail": "Records matching query not found. Check query parameter or offset parameter",
    },
    "status": "failure",
}


def test_lookup_with_no_match_raises(client, api):
    # What the Tufts instance actually returns for a query with no matches.
    api.get(f"{TABLE}/sys_user", status=404, json=NO_RECORD)

    # A LookupError, so to_payload can leave an unknown watcher off instead of failing the ticket.
    with pytest.raises(UnknownReference, match="found no"):
        client.sys_id_for("sys_user", "user_name", "nobody")


def test_find_treats_404_as_no_matches(client, api):
    api.get(f"{TABLE}/sys_user", status=404, json=NO_RECORD)

    assert client.find("sys_user", "user_name", "nobody") == []


def test_404_on_a_write_still_raises(client, api):
    api.patch(f"{TABLE}/incident/gone", status=404, json=NO_RECORD)

    with pytest.raises(ServiceNowError, match="404"):
        client.update_incident("gone", {"state": "6"})


def test_ambiguous_lookup_raises(client, api):
    api.get(f"{TABLE}/sys_user_group", json={"result": [{"sys_id": "a"}, {"sys_id": "b"}]})

    with pytest.raises(UnknownReference, match="more than one"):
        client.sys_id_for("sys_user_group", "name", "Research")


def test_query_values_cannot_add_conditions(client):
    with pytest.raises(ValueError):
        client.find("sys_user", "user_name", "jdoe01^ORactive=true")


def test_http_error_raises_with_status_and_body(client, api):
    api.post(f"{TABLE}/incident", status=403, json={"error": {"message": "Insufficient rights"}})

    with pytest.raises(ServiceNowError, match="403.*Insufficient rights"):
        client.create("incident", {"short_description": "x"})


def test_find_incident_by_correlation_id(client, api):
    api.get(
        f"{TABLE}/incident",
        match=[
            matchers.query_param_matcher({"sysparm_query": "correlation_id=coldfront:allocation:7"}, strict_match=False)
        ],
        json={"result": [{"sys_id": "i-1", "number": "INC0000001"}]},
    )

    assert client.find_incident("coldfront:allocation:7") == Incident(sys_id="i-1", number="INC0000001")


@pytest.mark.parametrize(("stored", "expected"), [("u-svc", "u-svc"), ("", None)])
def test_find_incident_reports_the_assignee(client, api, stored, expected):
    api.get(
        f"{TABLE}/incident",
        match=[matchers.query_param_matcher({"sysparm_fields": "sys_id,number,state,assigned_to"}, strict_match=False)],
        json={"result": [{"sys_id": "i-1", "number": "INC0000001", "state": "2", "assigned_to": stored}]},
    )

    assert client.find_incident("coldfront:allocation:7").assigned_to == expected


@pytest.mark.parametrize(("stored", "expected"), [("coldfront:allocation:7", "coldfront:allocation:7"), ("", None)])
def test_find_incident_by_number_reports_its_correlation_id(client, api, stored, expected):
    api.get(
        f"{TABLE}/incident",
        match=[
            matchers.query_param_matcher(
                {
                    "sysparm_query": "number=INC0000001",
                    "sysparm_fields": "sys_id,number,state,assigned_to,correlation_id",
                },
                strict_match=False,
            )
        ],
        json={"result": [{"sys_id": "i-1", "number": "INC0000001", "state": "1", "correlation_id": stored}]},
    )

    incident = client.find_incident_by_number("INC0000001")

    assert incident == Incident(sys_id="i-1", number="INC0000001")
    assert incident.correlation_id == expected


def test_find_incident_by_number_returns_none_when_absent(client, api):
    api.get(f"{TABLE}/incident", status=404, json=NO_RECORD)

    assert client.find_incident_by_number("INC0000001") is None


def test_find_incident_returns_none_when_absent(client, api):
    api.get(f"{TABLE}/incident", status=404, json=NO_RECORD)

    assert client.find_incident("coldfront:allocation:7") is None


def test_update_incident_patches_the_record(client, api):
    patch = api.patch(f"{TABLE}/incident/i-1", json={"result": {"sys_id": "i-1"}})

    client.update_incident("i-1", {"state": "6", "close_notes": "Allocation activated"})

    assert json.loads(patch.calls[0].request.body) == {"state": "6", "close_notes": "Allocation activated"}


def test_update_incident_resolves_the_assignee(client, api):
    user_lookup(api, "svc_test", "u-svc")
    patch = api.patch(f"{TABLE}/incident/i-1", json={"result": {"sys_id": "i-1"}})

    client.update_incident("i-1", {"assigned_to": "svc_test", "state": "6"})

    assert json.loads(patch.calls[0].request.body) == {"assigned_to": "u-svc", "state": "6"}


def test_update_incident_truncates_long_text(client, api):
    patch = api.patch(f"{TABLE}/incident/i-1", json={"result": {"sys_id": "i-1"}})

    client.update_incident("i-1", {"comments": "x" * 10_000})

    assert len(json.loads(patch.calls[0].request.body)["comments"]) == 4000
