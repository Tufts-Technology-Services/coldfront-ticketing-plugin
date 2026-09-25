"""Ticket model and PARSE_START/PARSE_END header builder.

NOT YET IMPLEMENTED. Adapted from rt-ticket-utils; the verbatim original is in docs/reference/ so
the two stay diffable. Copy models.Ticket and utils.create_parse_header; drop generate_text and its
Jinja2 PackageLoader (bodies are Django templates here), drop create_ticket_header (it hardcodes the
resolved-ticket defaults), and drop hpc_tickets.py entirely -- none of its builders match our events.

The header is a block of `key:value` lines between PARSE_START and PARSE_END, prepended to a
plain-text body, which the ticket system parses into fields. Despite the source library's name the
fields are ServiceNow's.

Three adaptations, all detailed in docs/plan.md:

1. Allow unresolved tickets. The original defaults to state='Resolved' /
   close_code='Resolved Remotely' and requires close_notes, because its existing callers file
   audit-trail tickets after automation already did the work. Three of our four events are pending
   requests and must open a ticket, not close one -- so state, close_code and close_notes become
   optional and settable.

2. Fix optional/list serialization. The original serializes a plain model_dump(), which emits
   `parent:None` when unset and `watch_list:['utln']` (a Python list repr) when set. Use
   exclude_none=True and join list values into a delimited string. This matters more once
   state/close_code are optional, since they would otherwise serialize as `state:None`.

3. Sanitize free text. Lines are `key:value` joined by newlines and the original only strips outer
   whitespace, so user-controlled text (project title, justification, PI name) containing a newline
   breaks the block -- and text containing `\\nstate:Resolved` injects a field outright. Collapse
   whitespace and truncate anything reaching a header field.

OPEN DECISION: whether Ticket stays a pydantic model or becomes a dataclass. pydantic is not
currently a ColdFront dependency, and the only pydantic features in use are extra='forbid' (a
dataclass gives the equivalent via TypeError on unknown kwargs) and str_strip_whitespace=True
(superseded by adaptation 3), so a dataclass would drop the dependency at no real loss. Resolve
before writing this module; pyproject.toml currently declares no pydantic dependency.
"""
