# Design

How coldfront-ticketing works, and why. The [README](../README.md) covers installing and using it;
this is the reasoning behind it, for anyone adopting or maintaining the plugin.

## The idea

ColdFront requests (a new allocation, an allocation change) become ServiceNow incidents, and those
incidents are resolved, with a message to the customer, once the work is actually done. The plugin
listens for ColdFront's Django signals, re-queries the models involved, and calls the ServiceNow
Table API from a background task (django-q2).

## Why signals, not ColdFront's emails

ColdFront already emails a ticket queue when a request is submitted. Those emails render from a very
narrow context: the helper that sends them passes a formatted PI name, the resource and a URL, and
drops everything else about the request. Making them richer would mean threading data through the
email plumbing at every call site.

Hooking the domain events instead decouples the ticket from whatever an email template needs. A
receiver gets a primary key, the task re-queries the model, and it has the whole ORM: the project,
the PI, the attributes requested, the justification. ColdFront's own ticket-queue emails are then
switched off (see [Companion ColdFront change](#companion-coldfront-change)), so each request
produces one ticket, not an email and an incident.

## Why the REST API, not email intake

A first design filed tickets by email, with a block of `key: value` fields for ServiceNow's inbound
email rules to parse. It was dropped because email can create a ticket but can't reliably update
it: ColdFront never learns the number of the incident its email created, and ServiceNow doesn't
thread inbound mail on arbitrary subject text. The Table API returns the new incident's `sys_id` and
number, and incidents can be found again by a field the plugin sets (`correlation_id`, below), so
the same incident is resolved later.

## Ticket slots

Each request or event has exactly one ticket: its **slot** (`ticketing/slots.py`). A slot is
identified by a deterministic `correlation_id`:

| Ticket | `correlation_id` |
|---|---|
| A new allocation | `coldfront:allocation:<pk>` |
| An allocation change request | `coldfront:allocation-change:<pk>` |
| A storage job failing for either | the request's, plus `:storage-failed` |
| A day's changes to a project's users | `coldfront:project-users:<project pk>:<YYYY-MM-DD>` |
| An HPC account | `coldfront:hpc-account:<username>` |

The plugin writes the `correlation_id` onto the incident it files, so every task can find "the
incident for this request" in ServiceNow by that one field. It's what lets the resolve task find
the incident the create task opened, and what makes every task safe to re-run: before filing, a task
checks for an incident with its `correlation_id` (locally first, then in ServiceNow) and uses that
instead. That matters because django-q2 re-queues a task whose run outlasts `retry`, so a slow API
call can be run twice.

Whichever incident fills a slot (one the plugin filed, or a customer's an admin linked; see
[Linking](#linking-tickets-filed-in-servicenow)) is recorded locally as a `FiledTicket`. That record
drives the ticket links in ColdFront and lets file-only tasks skip a ServiceNow lookup. Resolving
always asks ServiceNow, which has the incident's current state.

`AllocationTicketSlot` adds the allocation or change request a slot is for, which the six allocation
tasks and linking all use, so each kind of request is described in one place.

## Triggers

| When | What happens |
|---|---|
| An allocation or change request is submitted | An incident opens, unassigned, with an admin-facing summary. Not if a ticketing admin submitted it (below). |
| The storage plugin reports that it provisioned the request | The incident is resolved with a customer message. |
| A request the storage plugin doesn't provision is approved | The incident is resolved with a generic customer message. |
| The storage plugin reports a failure | An admin-only child ticket is filed. A later success resolves it too. |
| A project's users change | One already-resolved notice per project per day. |

### Requests open on submission

`allocation_new` and `allocation_change_created` queue the create task. The requestor is
`requestor_pk` if ColdFront sends it, else the logged-in user: these signals are sent from inside
the view, and simple-history's `HistoryRequestMiddleware` keeps the request where a receiver can
read it.

A sender view also matches its subclasses. Plugins replace core views (the storage plugin swaps in
its own `AllocationCreateView`), and matching one class exactly would depend on `INSTALLED_APPS`
order.

**Requests submitted by a ticketing admin open no ticket.** An admin usually submits a request on a
customer's behalf, for something the customer already asked for in ServiceNow. The request's
`correlation_id` only exists once it's submitted, so a ticket opened then would email the PI and
Managers about a second ticket before the customer's could be linked. The resolve still goes out: it
resolves the linked ticket, or files one already resolved, assigned to the admin who approved the
request.

### Storage requests resolve when the storage job succeeds

The storage plugin creates shares and sets quotas in background tasks after approval. Resolving on
approval could tell the customer "done" before, or despite, a failure, so the plugin resolves on the
outcome the storage plugin sends when a job finishes: `share_creation_successful`,
`quota_update_successful`, `share_creation_failed` or `quota_update_failed`, defined in coldfront-utils
(`coldfront_utils.signals`) so that this plugin doesn't depend on the storage plugin. Each job sends
one outcome; a retry it schedules for itself sends nothing until it finishes. The plugin used to watch
the allocation attributes the storage plugin records its state in, which also let a hand edit of one
resolve a ticket; now only a storage job can.

### What the storage plugin doesn't provision

Allocations on other resource types, and Storage change requests that don't change the quota (an
end-date extension, say), get no outcome signal: the storage plugin does nothing for them.
They're resolved when approved (`allocation_activate`, `allocation_change_approved`) instead.

`storage_will_provision` (`ticketing/allocation_updates.py`) decides which way a request goes, and
which message it gets. It copies the storage plugin's own conditions, and has to stay in step with
them: if it says "won't provision" when the plugin does act, the customer hears "done" before the
storage exists. Ideally the storage plugin would expose that rule itself.

### When a storage job fails

A failure (`share_creation_failed` or `quota_update_failed`) files a child of the request's incident
(ServiceNow's `parent` field), with only the approving admin on it, so no customer is emailed. Its
text is internal: what failed, the reason the storage plugin gave, the project and resource, and how
to run the job again. There's one per request: a repeat
failure adds a work note (reopening the ticket if it was resolved), and the success that resolves
the request resolves the failure ticket too.

### Project user notices

Adding or removing a project's users files one generic, already-resolved notice ("the users on your
project have changed") per project per day, adds and removes together. ColdFront's
`project_activate_user` and `project_remove_user` signals, from any sender, queue
`create_project_users_ticket` (`ticketing/tasks/project_users.py`). Later changes that day file
nothing. Within one request only the first signal queues a task, since tasks queued together could
each look for the day's ticket before any had filed it. Changes to ticketing admins, and to the
project's PI, file nothing.

### The admin switch

Ticketing admins can switch ticketing off for their own actions, from the menu bar. It's kept in
their session. Off means no ServiceNow calls for anything they do, resolves included.

Where the storage plugin resolves the request later, in its own task, the admin's request (and so
their switch) is no longer visible. So an approval made with the switch off leaves a private
`ticketing_suppressed` attribute on the allocation, holding the request's `correlation_id`. The
success that follows finds it, deletes it and resolves nothing. A failure in between leaves it in
place and files nothing.

### Out of scope

- **Denials** send no signal. Their incidents stay open for an admin, who usually needs to discuss
  the specifics with the requester anyway.
- **Allocation renewals and account upgrade requests** have no receivers.

### Not yet wired up

- **HPC account tickets** (`ticketing/tasks/hpc_accounts.py`): an admin-facing ticket while an
  account waits on a UID, and a welcome when it's created. How they're triggered (signals from an
  account plugin, or that plugin calling the tasks directly) is undecided.
- **Per-user group membership tickets** (`ticketing/tasks/groups.py`): a ticket for each user who
  joins or leaves a project's group. These are separate from the project user notices above, which
  are wired up and cover the same changes with one notice per project per day. The per-user tasks
  were superseded by them and are unused; if per-user tickets come back, bursts of changes would be
  better batched into one ticket.

## Who is on a ticket

- **Contact person** (`u_contact_person`): the requestor, whoever submitted the request.
- **Caller** (`caller_id`): who the request is for. ServiceNow notifies the caller. Without an
  explicit affected client it's the requestor. Without a known requestor (outside a web request)
  the PI stands in.
- **Watch list:** the project's PI and its Managers, less whoever is already on the ticket.
  Managers count only while Active with notifications enabled, the same filter ColdFront's own
  project emails use; the PI is always included. A watcher ServiceNow doesn't know is left off
  rather than failing the ticket.
- **`contact_type`:** `self-service` when the requestor is the project's PI or one of its Managers,
  `email` when someone else (in practice an admin) files on their behalf. Membership is tested
  directly, because ColdFront's `project.has_perm()` is true for every superuser. It's always sent:
  left unset, ServiceNow records `phone`.
- **Assignee:** request tickets open unassigned, waiting in the assignment group's queue. Anything
  resolved needs an assignee, so a resolve assigns `TICKETING_ASSIGNED_TO` if no one has picked it
  up, or the approving admin for an admin's request.

## Ticketing admins

Superusers, and users granted the `ticketing.manage_ticketing` permission (directly or through a
group). One test, `is_ticketing_admin`, decides who gets the controls (the switch, the ticket card,
the link form), whose requests open no ticket, and who is left out of project notices. Django's
`is_staff` alone doesn't count, matching ColdFront, whose admin views check `is_superuser`.

## What tickets say

- **Admin-facing request descriptions** (`allocation_request.txt`, `allocation_change_request.txt`)
  summarise the request: project, PI, requester, resource, attributes and the justification, with a
  link back to ColdFront.
- **Customer messages** are chosen by resource: the first of `<folder>/<resource name>.txt`,
  `<folder>/<resource type>.txt` and `<folder>/generic.txt` that exists. Storage has its own
  messages (a new share, a quota change). Everything else gets the generic ones, so a resource that
  needs its own wording only needs a template named after it or its type.
- **Only attributes the customer can see** (whose type isn't marked private) are listed, in
  customer messages and request descriptions alike: the PI and Managers are on those tickets.
- **Attribute names are made readable:** `slurm_account_name` becomes "Slurm account name". Known
  terms keep their own spelling (GPU, FreeIPA), and `TICKETING_KNOWN_TERMS` adds a site's own.

## Linking tickets filed in ServiceNow

Some requests reach ServiceNow first, by email or the portal, and an admin then makes the change in
ColdFront. From the ticket card on an allocation or project page, the admin links that incident to
the request it's for. Linking writes the request's `correlation_id` onto the incident and records
it, so the tasks treat it as their own: the create task finds it and files nothing, and the resolve
task resolves it with the customer message.

If the plugin had already opened its own incident for the request, that one is closed as a
duplicate of the linked one. Linking is refused for an incident linked to something else, or one
already resolved. A link can be undone until the ticket is resolved: unlinking clears the
`correlation_id` (with an internal work note) and forgets the record, so the request can be linked
again, or the plugin files its own when it resolves.

## ServiceNow notes

- **Reference fields** (caller, contact, assignee, watch list, assignment group, business service,
  parent) take `sys_id`s. The payload (`ServiceNowIncident`) holds ColdFront's terms (usernames,
  group names, incident numbers) and resolves them when it's sent.
- **Invalid choice values are dropped without an error**: the API returns 200 and leaves the field
  empty. Check every choice value against `/api/now/ui/meta/incident` on your instance; the plugin's
  are in `ticketing/tickets.py`, with the payload's defaults (category, assignment group, business
  service), which are site-specific.
- **Unknown fields are dropped silently too**, so check field names against that endpoint as well.
- **Omit, don't null:** sending `null` on an update blanks the field, so payloads leave out unset
  fields.
- **Empty queries return 404** ("No Record found"), not an empty result.

### ServiceNow requests instead of incidents

Everything is filed as an incident today. A slot already records which table holds its ticket
(`table`, always `incident`), and nothing on the slot is incident-specific, so moving a kind of
ticket to Service Catalog requests would be a change to its slot. The rest would need a small
strategy per record type behind `open_ticket` and `resolve_or_file`, chosen by the slot's table:

- **creating:** request items are ordered through the Service Catalog API (which returns a request
  whose item is then looked up), not the Table API;
- **finding and updating:** by `correlation_id` in that table;
- **resolving:** request items close with "Closed Complete" and no close code, rather than
  "Resolved" with one;
- **building:** a request item has `requested_for` rather than a caller, and none of the incident's
  custom classification fields, so it needs its own payload class.

A catalog item and flow would also be needed on the ServiceNow side.

## Companion ColdFront change

The plugin works with stock ColdFront signals. A small change to ColdFront itself completes it:

- **`EMAIL_TICKET_SYSTEM_NOTIFICATIONS_ENABLED`** (default `True`), gating the helper that emails
  the ticket queue, so core's ticket-queue emails can be switched off once the plugin takes over.
  Without it, every request produces an emailed ticket as well as an incident.
- **`requestor_pk`** on the two request signals (optional). The receivers already read the
  submitter from the current request; this would make it explicit, and independent of the request
  middleware.
- **`approver_pk`** on the two approval signals (optional), for recording who approved.

## Operations

- **Raise django-q2's `retry` above `timeout`.** ColdFront sets both to 120 seconds, and a task is
  re-queued once `retry` elapses, so a slow ServiceNow call can be run again while still in flight.
  The `correlation_id` check makes that safe, but it's wasted work.
- **Queueing never breaks the user's request.** `enqueue_ticket` queues after the transaction
  commits, and logs rather than raises if the broker (Redis) is down, so a submission doesn't fail
  because ticketing couldn't be queued.
- **If `qcluster` isn't running, tasks queue silently** and no ticket is filed. Once core's emails
  are gated off there's no backstop, so either keep the gate on until `qcluster` is running
  reliably, or run django-q2 synchronously for a while.
- **A task that raises** (ServiceNow down, say) is recorded as a django-q2 failure, not retried.
- **Rolling out:** leave core's ticket emails on at first, check incidents are created and resolved
  correctly while accepting an extra emailed ticket per request, then switch the emails off.
