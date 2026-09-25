from ticketing.tickets import SHORT_DESCRIPTION_MAX_LENGTH, Ticket, create_parse_header


def parse(header):
    block = header.split("PARSE_START\n")[1].split("\nPARSE_END")[0]
    return dict(line.split(":", 1) for line in block.splitlines())


def make(**overrides):
    kwargs = {
        "u_contact_person": "jdoe01",
        "caller_id": "jdoe01",
        "short_description": "New allocation request",
        "assigned_to": "svc_test",
    }
    kwargs.update(overrides)
    return Ticket(**kwargs)


def test_round_trips_field_values():
    fields = parse(create_parse_header(make()))

    assert fields["u_contact_person"] == "jdoe01"
    assert fields["caller_id"] == "jdoe01"
    assert fields["short_description"] == "New allocation request"
    assert fields["assigned_to"] == "svc_test"
    assert fields["assignment_group"] == "TTS Research Technology"


def test_unset_resolution_fields_are_omitted():
    fields = parse(create_parse_header(make()))

    assert "state" not in fields
    assert "close_code" not in fields
    assert "close_notes" not in fields
    assert "parent" not in fields
    assert "watch_list" not in fields


def test_resolution_fields_are_emitted_when_set():
    ticket = make(state="Resolved", close_code="Resolved Remotely", close_notes="Done")
    fields = parse(create_parse_header(ticket))

    assert fields["state"] == "Resolved"
    assert fields["close_code"] == "Resolved Remotely"
    assert fields["close_notes"] == "Done"


def test_watch_list_renders_delimited_not_as_list_repr():
    fields = parse(create_parse_header(make(watch_list=["asmith02", "bjones03"])))

    assert fields["watch_list"] == "asmith02,bjones03"


def test_empty_watch_list_is_omitted():
    assert "watch_list" not in parse(create_parse_header(make(watch_list=[])))


def test_newline_in_free_text_cannot_break_the_block():
    ticket = make(short_description="Genomics\nproject")
    fields = parse(create_parse_header(ticket))

    assert fields["short_description"] == "Genomics project"


def test_newline_in_free_text_cannot_inject_a_field():
    ticket = make(short_description="Genomics\nstate:Resolved")
    fields = parse(create_parse_header(ticket))

    assert "state" not in fields
    assert fields["short_description"] == "Genomics state:Resolved"


def test_colons_in_values_survive():
    fields = parse(create_parse_header(make(short_description="Storage: Creation | 5TB")))

    assert fields["short_description"] == "Storage: Creation | 5TB"


def test_short_description_is_truncated():
    fields = parse(create_parse_header(make(short_description="x" * 500)))

    assert len(fields["short_description"]) == SHORT_DESCRIPTION_MAX_LENGTH
