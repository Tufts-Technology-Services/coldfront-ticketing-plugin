"""Ticket model and PARSE_START/PARSE_END header builder.

Adapted from rt-ticket-utils; the verbatim original is in docs/reference/ and CLAUDE.md explains
what changed and why. Pure stdlib -- no Django or ColdFront imports, so it is testable standalone.
"""

import re
from dataclasses import asdict, dataclass

SHORT_DESCRIPTION_MAX_LENGTH = 160
LIST_DELIMITER = ","

_WHITESPACE = re.compile(r"\s+")


@dataclass(kw_only=True)
class Ticket:
    """Fields the ticket system parses out of the header block.

    state/close_code/close_notes are resolution fields: leave them unset to open a ticket, set them
    to file one that is already resolved.

    Declared in the original library's field order, which kw_only makes possible despite the
    required fields being interleaved with defaulted ones.
    """

    u_incident_type: str = "Request"
    u_contact_person: str
    caller_id: str
    source: str = "Email"
    category: str = "Research"
    subcategory: str = "Research Systems"
    u_business_service: str = "High Performance Computing (HPC)"
    short_description: str
    assigned_to: str
    assignment_group: str = "TTS Research Technology"
    parent: str | None = None
    watch_list: list[str] | None = None
    u_action: str = "Activate/Setup/Install"
    state: str | None = None
    close_code: str | None = None
    close_notes: str | None = None


def _clean(value) -> str:
    """Collapse all whitespace to single spaces.

    Header lines are `key:value` joined by newlines, so a value containing a newline would split
    into a bogus line -- or inject a field outright (`\\nstate:Resolved`). Values reaching here
    include user-supplied text such as project titles and justifications. Colons are left alone:
    the parser splits on the first one, and existing ticket titles legitimately contain them.
    """
    return _WHITESPACE.sub(" ", str(value)).strip()


def create_parse_header(ticket: Ticket) -> str:
    """Render the ticket as a PARSE_START/PARSE_END block to prepend to an email body."""
    lines = []
    for key, value in asdict(ticket).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = LIST_DELIMITER.join(_clean(item) for item in value if _clean(item))
            if not value:
                continue
        else:
            value = _clean(value)
        if key == "short_description":
            value = value[:SHORT_DESCRIPTION_MAX_LENGTH]
        lines.append(f"{key}:{value}")

    return """
PARSE_START
{0}
PARSE_END

""".format("\n".join(lines))
