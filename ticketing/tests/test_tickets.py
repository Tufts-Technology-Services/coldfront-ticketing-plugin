from ticketing.tickets import (
    LONG_TEXT_MAX_LENGTH,
    SHORT_DESCRIPTION_MAX_LENGTH,
    TRUNCATION_MARKER,
    Ticket,
    fit_update,
    to_payload,
)


def fake_resolve(table, column, value):
    """Stands in for ServiceNowClient.sys_id_for: a readable fake sys_id per lookup."""
    return f"<{table}.{column}={value}>"


def make(**overrides):
    kwargs = {
        "u_contact_person": "jdoe01",
        "caller_id": "jdoe01",
        "short_description": "New allocation request",
        "assigned_to": "svc_test",
    }
    kwargs.update(overrides)
    return Ticket(**kwargs)


def test_people_fields_resolve_from_utln_to_sys_user():
    payload = to_payload(make(), fake_resolve)

    assert payload["caller_id"] == "<sys_user.user_name=jdoe01>"
    assert payload["u_contact_person"] == "<sys_user.user_name=jdoe01>"
    assert payload["assigned_to"] == "<sys_user.user_name=svc_test>"


def test_group_and_parent_resolve_by_name_and_number():
    payload = to_payload(make(parent="INC0012345"), fake_resolve)

    assert payload["assignment_group"] == "<sys_user_group.name=TTS Research Technology>"
    assert payload["parent"] == "<task.number=INC0012345>"


def test_watch_list_resolves_each_member_and_joins():
    payload = to_payload(make(watch_list=["asmith02", "bjones03"]), fake_resolve)

    assert payload["watch_list"] == "<sys_user.user_name=asmith02>,<sys_user.user_name=bjones03>"


def test_non_reference_fields_pass_through():
    payload = to_payload(make(category="Research", correlation_id="coldfront:allocation:7"), fake_resolve)

    assert payload["category"] == "Research"
    assert payload["correlation_id"] == "coldfront:allocation:7"
    assert payload["short_description"] == "New allocation request"


def test_unset_fields_are_omitted_not_sent_as_null():
    payload = to_payload(make(), fake_resolve)

    for field in ("state", "close_code", "close_notes", "parent", "contact_type", "description", "comments"):
        assert field not in payload


def test_empty_watch_list_is_omitted():
    assert "watch_list" not in to_payload(make(watch_list=[]), fake_resolve)


def test_there_is_no_source_field():
    # The email intake's `source` is not an incident column; the API would silently drop it.
    assert "source" not in to_payload(make(), fake_resolve)


def test_short_description_collapses_whitespace():
    payload = to_payload(make(short_description="Genomics\n  project"), fake_resolve)

    assert payload["short_description"] == "Genomics project"


def test_short_description_is_truncated():
    payload = to_payload(make(short_description="x" * 500), fake_resolve)

    assert len(payload["short_description"]) == SHORT_DESCRIPTION_MAX_LENGTH


def test_description_keeps_its_line_breaks():
    payload = to_payload(make(description="Resource: Tier 1\nQuota: 5 TB"), fake_resolve)

    assert payload["description"] == "Resource: Tier 1\nQuota: 5 TB"


def test_description_at_the_limit_is_untouched():
    text = "x" * LONG_TEXT_MAX_LENGTH

    assert to_payload(make(description=text), fake_resolve)["description"] == text


def test_long_text_fields_are_truncated_with_a_marker():
    long = "x" * 10_000
    payload = to_payload(make(description=long, comments=long, work_notes=long, close_notes=long), fake_resolve)

    for field in ("description", "comments", "work_notes", "close_notes"):
        assert len(payload[field]) == LONG_TEXT_MAX_LENGTH
        assert payload[field].endswith(TRUNCATION_MARKER)


def test_fit_update_truncates_long_text_and_leaves_the_rest():
    fields = fit_update({"state": "6", "close_notes": "x" * 10_000, "comments": "short"})

    assert fields["state"] == "6"
    assert fields["comments"] == "short"
    assert len(fields["close_notes"]) == LONG_TEXT_MAX_LENGTH
