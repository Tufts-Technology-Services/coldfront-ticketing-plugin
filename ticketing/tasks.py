"""Task functions that build and send one ticket email each.

NOT YET IMPLEMENTED. Invoked via django_q async_task from ticketing.signals.

One task per event. Each receives pks, re-queries, and composes in three steps:

1. Build a Ticket (see ticketing.tickets) with state/close_code appropriate to a *pending request*
   rather than completed work. People-fields are UTLNs, which equal User.username in ColdFront:
   u_contact_person and caller_id from the requestor, watch_list from project.pi.username,
   assigned_to from the TICKETING_ASSIGNED_TO setting. Sanitize any free text reaching
   short_description.

2. Render the narrative body with django.template.loader.render_to_string from
   templates/ticketing/. This is where the re-queried detail lands -- allocation attributes,
   resource, project, quota, dates -- and is the entire reason the plugin hooks signals rather than
   widening ColdFront's email context. coldfront_utils (attribute/attribute.py, util/allocations.py)
   has the attribute helpers; prefer them over hand-rolled lookups.

3. Concatenate create_parse_header(ticket) + body and send with ColdFront's own
   coldfront.core.utils.mail.send_email(subject, body, sender, [EMAIL_TICKET_SYSTEM_ADDRESS]).
   Reusing that helper inherits the EMAIL_ENABLED kill switch, the DEBUG recipient redirect, the
   subject prefix, and SMTPException handling.

   Do NOT use send_email_template -- the body is already rendered, and a second template pass would
   mangle the parse block.
"""
