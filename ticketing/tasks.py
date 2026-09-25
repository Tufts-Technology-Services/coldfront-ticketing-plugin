"""Task functions that create and resolve ServiceNow incidents.

NOT YET IMPLEMENTED. Invoked via django_q async_task from ticketing.signals.

Four tasks: create and resolve an allocation incident; create and resolve a change-request
incident. The two resolve tasks can share one implementation, keyed on which mapping to look up.

Create:

1. Re-query by pk. Skip if the local allocation/change-request -> incident mapping (ticketing.models)
   already has an entry, or if ServiceNow already has an incident with this object's correlation_id
   (e.g. "coldfront:allocation:<pk>"). The second check covers a task that died between the POST and
   saving the mapping, so a retried task never opens a duplicate incident.

2. Build a Ticket (ticketing.tickets) with state/close_code unset so the incident opens. People
   fields are UTLNs, which equal User.username in ColdFront: u_contact_person and caller_id from the
   requestor, watch_list from project.pi, assigned_to from TICKETING_ASSIGNED_TO. Over the Table API
   these are reference fields, so they likely need converting to sys_user sys_ids first (open
   question 3 in docs/plan.md). Truncate short_description; it is single-line. Set contact_type
   explicitly -- left unset, the instance fills in "phone": "self-service" if the requestor is the
   project's PI or an active Manager on it, else "email" (a superuser filing on someone's behalf).
   Check membership directly; project.has_perm() is True for every superuser. See "Choosing
   contact_type" in docs/plan.md.

3. Render the incident description with django.template.loader.render_to_string from
   templates/ticketing/. This is where the re-queried detail lands -- allocation attributes,
   resource, project, quota, dates -- and is the reason the plugin hooks signals at all.
   coldfront_utils (attribute/attribute.py, util/allocations.py) has the attribute helpers; prefer
   them over hand-rolled lookups.

4. Create the incident via ticketing.client and store its sys_id and number in the mapping.

Resolve:

1. Look up the stored incident. If there is none (the object predates the plugin, was created
   outside the request views, or the create task hasn't finished yet), log and return.

2. Update that sys_id with the resolution fields (state, close_code, close_notes) and a
   customer-facing comment rendered from templates/ticketing/. Omit unset fields: sending null on
   an update would blank the field in ServiceNow.
"""
