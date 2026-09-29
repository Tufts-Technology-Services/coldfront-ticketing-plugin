# coldfront-ticketing

A ColdFront plugin that opens and resolves ServiceNow incidents for allocation requests and
allocation change requests.

It listens for Django signals, re-queries the affected models, and calls the ServiceNow Table API:
an incident is created when a request is submitted and resolved when the request is approved.

## Status

**Early.** The design is settled (`docs/plan.md`). The ServiceNow client (`client.py`) and the
incident payload (`tickets.py`) are written and tested; the mapping model, the signal receivers
(`signals.py`) and the tasks (`tasks.py`) are not. Several details
wait on answers from the ServiceNow team — see "Open questions" in `docs/plan.md`.

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
| New allocation request | `allocation_new` | `AllocationCreateView` | Create incident |
| Allocation activated | `allocation_activate` | `AllocationDetailView` | Resolve that incident |
| New allocation change request | `allocation_change_created` | `AllocationChangeView` | Create incident |
| Change request approved | `allocation_change_approved` | `AllocationChangeDetailView` | Resolve that incident |

Denials send no signal; their incidents stay open for admins to handle manually. Allocation renewals
and account upgrade requests are unused at Tufts and out of scope. See "Out of scope" in
`docs/plan.md`.

## Requires a companion ColdFront change

All four signals already exist in ColdFront. A companion PR to the ColdFront fork adds the
`requestor_pk` kwarg to the two request signals (without it the submitter can't be recovered) and
the `EMAIL_TICKET_SYSTEM_NOTIFICATIONS_ENABLED` gate that switches off ColdFront's own ticket-queue
emails. See "Part A" in `docs/plan.md`.

## Install

Add the package to the deployment's dependencies, then in ColdFront's `local_settings.py`:

```python
import os

INSTALLED_APPS += ["ticketing"]

TICKETING_ENABLE_SIGNALS = True
TICKETING_ASSIGNED_TO = "<service-account-utln>"

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

### Admin ticketing switch

Superusers get a "Tickets on / Tickets off" control in the menu bar. With it off, nothing they do
files or resolves a ticket, until they switch it back on or their session ends. The site's navbar
template must include it, guarded so the template still renders where the plugin isn't installed:

```django
{% if ticketing_toggle %}{% include 'ticketing/navbar_toggle.html' %}{% endif %}
```

The switch relies on simple-history's `HistoryRequestMiddleware`, which is in ColdFront's default
`MIDDLEWARE`. If it's missing, the control shows "Ticket switch unavailable" and refuses to switch.

## Layout

```
ticketing/
  apps.py       AppConfig; ready() imports signals when TICKETING_ENABLE_SIGNALS
  signals.py    receivers for the four events -> enqueue_ticket
  dispatch.py   enqueue_ticket: the one way tasks get queued; honours the admin switch
  toggle.py     the admin switch, kept in the session
  views.py, urls.py, context_processors.py   the menu-bar control (htmx)
  tasks.py      re-query by pk, build the incident payload, create or resolve via the API
  client.py     ServiceNow Table API client (requests)
  tickets.py    Ticket dataclass + incident payload builder (adapted from rt-ticket-utils)
  templates/ticketing/   Django templates for incident description and resolution text
  tests/        settings.py layers on coldfront's real settings; conftest.py fixtures
docs/
  plan.md           full design, rationale, and open questions
  email_usage.md    inventory of ColdFront's existing email behavior
  reference/        verbatim rt-ticket-utils source the vendored code derives from
```

Planned addition: `models.py` (allocation / change request → incident mapping).
