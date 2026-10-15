# coldfront-ticketing

A ColdFront plugin that opens and resolves ServiceNow incidents for allocation requests and
allocation change requests, and files notices when a project's users change.

It listens for Django signals, re-queries the affected models, and calls the ServiceNow Table API:
an incident is created when a request is submitted and resolved once the storage plugin reports the
approved work done.

## Status

**Not yet deployed.** The ServiceNow client (`client.py`), the incident payload (`tickets.py`), the
tasks (`tasks.py`) and the signal receivers (`signals.py`) are written and tested, and the
allocation lifecycles have been trial-run on a development instance. Requests open a ticket when
submitted (unless a ticketing admin submitted them); the storage plugin's success resolves it. See
`docs/plan.md` for the status of each piece and what is still open.

`ticketing/tickets.py` holds the `Ticket` dataclass and `to_payload`, which turns it into the
Table API body, resolving UTLNs and group names to sys_ids.

`tickets.py` has no Django or ColdFront imports, so its tests run standalone:

```sh
python3 -c "
from ticketing.tests import test_tickets as t
[getattr(t, n)() for n in dir(t) if n.startswith('test_')]
print('ok')
"
```

The full suite runs with `uv run pytest` (`ticketing/tests/settings.py` layers on ColdFront's own
settings).

## Events covered

| Event | Signal | Sender | Action |
|---|---|---|---|
| New allocation request | `allocation_new` | `AllocationCreateView` | Create incident, unless a ticketing admin submitted it |
| Share created | `post_save`: `share_creation_state` = `success` | `AllocationAttribute` | Resolve the allocation's incident (assigned to the approver if a ticketing admin requested it) |
| New allocation change request | `allocation_change_created` | `AllocationChangeView` | Create incident, unless a ticketing admin submitted it |
| Quota changed | `post_save`: `quota_update_state` = `success` | `AllocationAttribute` | Resolve the change request's incident (assigned to the approver if a ticketing admin requested it) |
| Allocation or change approved | `allocation_activate`, `allocation_change_approved` | `AllocationDetailView`, `AllocationChangeDetailView` | Nothing, unless the approving admin's switch is off (see below) |
| Project users added or removed | `project_activate_user`, `project_remove_user` | any | File a resolved notice, once per project per day |

A sender view also matches its subclasses: the storage plugin, for one, replaces
`AllocationCreateView` with its own subclass, so matching the class exactly would depend on the
order of `INSTALLED_APPS`.

**Requests resolve on storage success, not on approval.** The storage plugin creates shares and
sets quotas in background tasks after approval, and records each outcome in an allocation
attribute. Resolving on approval could tell the customer "done" before, or despite, a failure. A
failure leaves the incident open for an admin. The resolve runs in the storage plugin's task, where
the approving admin's switch isn't visible, so approving with the switch off leaves a private
`ticketing_suppressed` marker on the allocation and that request's resolve is skipped. See
"Triggers" in `docs/plan.md`.

**Not yet resolved automatically:** allocations on non-Storage resources, Storage change requests
with no quota change (an end-date extension, say), and failed storage jobs. Their incidents stay
open for an admin; a ticketing admin's request in this group files nothing. Resolving them on
approval, and filing an admin-only ticket when a storage job fails, is planned. See "Requests the
storage plugin doesn't finish" in `docs/plan.md`.

**Project user notices are once per calendar day, by design.** The first add or remove on a project
files a generic, already-resolved notice ("the users on your project have changed"). Every later
change to that project's users the same day files nothing. That includes a removal after a morning
add, so the PI and Approvers aren't told about it. The next day's first change files a new notice.

**Adding or removing the PI files nothing.** Tufts' project-create view sends
`project_activate_user` for the new project's PI, so without this every new project would get a
notice that its users had changed. A PI isn't otherwise added to or removed from a project.
Ticketing admins (see "Ticketing admins" below) are skipped too: they aren't project members
to notify. See "Project user tickets" in `docs/plan.md`.

Denials send no signal; their incidents stay open for admins to handle manually. Allocation renewals
and account upgrade requests are unused at Tufts and out of scope. See "Out of scope" in
`docs/plan.md`.

## Requires a companion ColdFront change

All these signals already exist in ColdFront. A companion PR to the ColdFront fork adds the
`requestor_pk` kwarg to the two request signals (until then the receivers take the submitter from
the current request, which works only while the signal is sent from a view) and
the `EMAIL_TICKET_SYSTEM_NOTIFICATIONS_ENABLED` gate that switches off ColdFront's own ticket-queue
emails. See "Part A" in `docs/plan.md`.

## Install

Add the package to the deployment's dependencies, then in ColdFront's `local_settings.py`:

```python
import os

INSTALLED_APPS += ["ticketing"]

TICKETING_ENABLE_SIGNALS = True
TICKETING_ASSIGNED_TO = "<service-account-utln>"  # assignee of resolved tickets
# Only if the storage plugin's SHARE_CREATION_STATE_ATTRIBUTE_NAME / QUOTA_UPDATE_STATE_ATTRIBUTE_NAME
# are overridden; these must match them.
# TICKETING_SHARE_STATE_ATTRIBUTE = "share_creation_state"
# TICKETING_QUOTA_STATE_ATTRIBUTE = "quota_update_state"

# ServiceNow connection. Read credentials from the environment rather than committing them.
SERVICENOW_URL = os.environ["SERVICENOW_URL"]
SERVICENOW_USERNAME = os.environ["SERVICENOW_USERNAME"]
SERVICENOW_PASSWORD = os.environ["SERVICENOW_PASSWORD"]

# Hand ticket creation over to this plugin
EMAIL_TICKET_SYSTEM_NOTIFICATIONS_ENABLED = False

# django-q2: retry must exceed timeout or slow API calls get re-run while still in flight
Q_CLUSTER["retry"] = 600

# The admin ticketing switch in the menu bar
TEMPLATES[0]["OPTIONS"]["context_processors"].append("ticketing.context_processors.ticketing_toggle")
```

and in `local_urls.py`:

```python
from django.urls import include, path
from coldfront.config.urls import urlpatterns

urlpatterns.append(path("ticketing/", include("ticketing.urls")))
```

### Ticketing admins

The plugin's controls are for **ticketing admins**: superusers, and users granted the
`ticketing.manage_ticketing` permission ("Can use the ServiceNow ticketing controls"), directly or
through a group, in Django admin. Django's `is_staff` alone doesn't count. Ticketing admins:

- get the ticketing switch, the ticket card and its link form;
- can view filed tickets in Django admin (which itself admits only `is_staff` users);
- open no ticket when they submit an allocation or change request (see the link form, below);
- file no project user notice when they're added to or removed from a project.

### Admin ticketing switch

Ticketing admins get a "Tickets on / Tickets off" control in the menu bar. With it off, nothing they do
files or resolves a ticket, until they switch it back on or their session ends. The site's navbar
template must include it, guarded so the template still renders where the plugin isn't installed:

```django
{% if ticketing_toggle %}{% include 'ticketing/navbar_toggle.html' %}{% endif %}
```

The switch relies on simple-history's `HistoryRequestMiddleware`, which is in ColdFront's default
`MIDDLEWARE`. If it's missing, the control shows "Ticket switch unavailable" and refuses to switch.

### Links to filed tickets

Each ticket the plugin files is recorded as a `FiledTicket` (run `manage.py migrate` after
installing): the incident number and `sys_id`, what it's about, and when the plugin resolved it.
Ticketing admins can see them in two places:

- **Django admin**, under Ticketing, read-only. This is the only place HPC account tickets show,
  since ColdFront has no page for a user.
- **A "ServiceNow Tickets" card** on allocation and project pages, linking each ticket. A project's
  card includes its allocations' tickets, and an allocation's includes its change requests'. The
  site's `allocation/allocation_detail.html` and `project/project_detail.html` overrides include it,
  guarded like the switch:

  ```django
  {% if ticketing_toggle %}{% include 'ticketing/filed_tickets.html' with subject=allocation %}{% endif %}
  {% if ticketing_toggle %}{% include 'ticketing/filed_tickets.html' with subject=project %}{% endif %}
  ```

  The card also has a **"Link a ServiceNow ticket"** form, for requests filed in ServiceNow directly
  (by email or the portal). Give it the incident number and what it's for (the allocation request, a
  change request, or today's project user changes): ColdFront then resolves that incident instead of
  filing its own, and closes any incident it had already opened for the request as a duplicate.
  The card shows for ticketing admins even with no tickets, for the form.

  Requests submitted by ticketing admins open no ticket, since one would email
  the PI and Approvers before the customer's ServiceNow ticket could be linked. They're still
  resolved, assigned to the admin who approved them. See "Linking tickets filed in ServiceNow" in
  `docs/plan.md`.

Links are built from `SERVICENOW_URL` when shown, so they follow the instance the site is configured
for. Records survive deleting what they're about (the link is cleared, the `correlation_id` stays).
Deleting a record only forgets the link; the ticket stays in ServiceNow.

## Layout

```
ticketing/
  apps.py       AppConfig; ready() imports signals when TICKETING_ENABLE_SIGNALS
  signals.py    requests, storage success, project user changes -> enqueue_ticket
  dispatch.py   enqueue_ticket: the one way tasks get queued; honours the admin switch
  toggle.py     the admin switch, kept in the session
  permissions.py   is_ticketing_admin: superusers and holders of ticketing.manage_ticketing
  views.py, urls.py, context_processors.py   the menu-bar control (htmx)
  tasks.py      re-query by pk, build the incident payload, create or resolve via the API
  models.py     FiledTicket: each ticket filed, what it's about, and its ServiceNow link
  admin.py      FiledTicket, read-only
  linking.py    adopt a ticket filed in ServiceNow (the card's link form, LinkTicketView)
  templatetags/ticketing_tags.py   filed_tickets_for, link_form_for, for the ticket card
  client.py     ServiceNow Table API client (requests)
  tickets.py    Ticket dataclass + incident payload builder (adapted from rt-ticket-utils)
  templates/ticketing/   Django templates for incident description and resolution text
  tests/        settings.py layers on coldfront's real settings; conftest.py fixtures
docs/
  plan.md           full design, rationale, and open questions
  email_usage.md    inventory of ColdFront's existing email behavior
  reference/        verbatim rt-ticket-utils source the vendored code derives from
```
