# coldfront-ticketing

A ColdFront plugin that files tickets into Tufts' ticketing system when allocation and account
events happen in ColdFront.

It listens for Django signals, re-queries the affected models, and emails the ticket queue a message
carrying a `PARSE_START` / `PARSE_END` field block that the ticket system parses into a ticket.

## Status

**Scaffold only — not yet implemented.** The structure, conventions, and design are settled; the
receiver, task, and ticket-building code are stubs. See `docs/plan.md` for the full design and
`CLAUDE.md` for project orientation, including two decisions still open.

## Events covered

| Event | Signal | Sender |
|---|---|---|
| New allocation request | `allocation_new` | `AllocationCreateView` |
| Allocation renewed | `allocation_renewed` | `AllocationRenewView` |
| New allocation change request | `allocation_change_created` | `AllocationChangeView` |
| Account upgrade request | `user_upgrade_requested` | `UserUpgradeAccount` |

## Requires a companion ColdFront change

Two of those signals (`allocation_renewed`, `user_upgrade_requested`), the `requestor_pk` kwarg on
the allocation signals, and the `EMAIL_TICKET_SYSTEM_NOTIFICATIONS_ENABLED` gate that suppresses
ColdFront's own ticket emails are all added by a companion PR to the ColdFront fork. This plugin
cannot function until that lands. See "Core-side dependency" in `CLAUDE.md`.

## Install

Add the package to the deployment's dependencies, then in ColdFront's `local_settings.py`:

```python
INSTALLED_APPS += ["ticketing"]

TICKETING_ENABLE_SIGNALS = True
TICKETING_ASSIGNED_TO = "<service-account-utln>"

# Hand ticket creation over to this plugin
EMAIL_TICKET_SYSTEM_NOTIFICATIONS_ENABLED = False

# django-q2: retry must exceed timeout or slow sends get re-run and duplicate tickets
Q_CLUSTER["retry"] = 600
```

The ticket queue's intake address is read from ColdFront's existing `EMAIL_TICKET_SYSTEM_ADDRESS`.

## Layout

```
ticketing/
  apps.py       AppConfig; ready() imports signals when TICKETING_ENABLE_SIGNALS
  signals.py    receivers for the four events -> async_task
  tasks.py      re-query by pk, build ticket, render body, send
  tickets.py    Ticket model + parse-header builder (adapted from rt-ticket-utils)
  templates/ticketing/   Django templates for ticket bodies
  tests/        settings.py layers on coldfront's real settings; conftest.py fixtures
docs/
  plan.md           full design and rationale
  email_usage.md    inventory of ColdFront's existing email behavior
  reference/        verbatim rt-ticket-utils source the vendored code derives from
```
