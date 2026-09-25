# Email usage in ColdFront

Inventory of everywhere ColdFront sends email, for planning a local-ticketing-system
integration. All email in this codebase goes through `django.core.mail.EmailMessage`,
wrapped by a handful of shared helpers — there is no per-app ad-hoc SMTP code, and no
plugin (`coldfront/plugins/*`) sends its own email.

## 1. Shared helpers — `coldfront/core/utils/mail.py`

This is the only file that imports `EmailMessage` directly.

- `send_email(subject, body, sender, receiver_list, cc=None)` (`mail.py:27`) — base
  sender. No-ops if `EMAIL_ENABLED` is false; prefixes the subject with
  `EMAIL_SUBJECT_PREFIX`; **redirects every recipient (and cc) to
  `EMAIL_DEVELOPMENT_EMAIL_LIST` whenever `settings.DEBUG` is true**; sends via
  `EmailMessage(...).send(fail_silently=False)`, catching `SMTPException`.
- `send_email_template(subject, template_name, template_context, receiver_list, sender=EMAIL_SENDER, cc=None)`
  (`mail.py:57`) — renders a template with `render_to_string`, merges in
  `email_template_context()`, calls `send_email`. This is the workhorse; almost every
  call site below goes through it.
- `email_template_context()` (`mail.py:76`) — base context (`center_name`,
  `signature`, `opt_out_instruction_url`, `center_base_url`) merged into every
  templated email.
- `build_link(url_path, domain_url="")` (`mail.py:86`) — builds an absolute URL for
  email bodies, defaulting to `CENTER_BASE_URL`.
- **`send_admin_email_template(subject, template_name, template_context)`**
  (`mail.py:92`) — sends straight to `[EMAIL_TICKET_SYSTEM_ADDRESS]`. **This is the
  existing seam for a ticketing system.**
- `send_allocation_admin_email(allocation_obj, subject, template_name, url_path="", domain_url="")`
  (`mail.py:102`) — builds PI/resource context + a link to `allocation-request-list`,
  then calls `send_admin_email_template` → `EMAIL_TICKET_SYSTEM_ADDRESS`.
- `send_allocation_customer_email(...)` (`mail.py:123`) — sends to allocation
  users who have `enable_notifications` on (PI/researchers), not admins.
- `send_allocation_eula_customer_email(...)` (`mail.py:142`) — EULA accept/decline
  confirmations to one allocation user, optionally CC'ing project managers.

## 2. Settings (`coldfront/config/email.py`, unless noted)

| Setting | Line | Purpose |
|---|---|---|
| `EMAIL_ENABLED` | 10 | Master on/off switch |
| `EMAIL_BACKEND` | 11 | Hardcoded `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST` / `EMAIL_PORT` | 12-13 | SMTP host/port |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | 14-15 | SMTP auth |
| `EMAIL_USE_TLS` | 16 | SMTP TLS |
| `EMAIL_TIMEOUT` | 17 | SMTP timeout |
| `EMAIL_SUBJECT_PREFIX` | 18 | Default `[ColdFront]` |
| `EMAIL_ADMIN_LIST` | 19 | Admin address list; used in `allocation/tasks.py` and as default CC in `project/forms.py` |
| `EMAIL_SENDER` | 20 | Default "From" address |
| `EMAIL_TICKET_SYSTEM_ADDRESS` | 21 | **Existing ticket-queue mailbox address** — the natural integration point |
| `EMAIL_DIRECTOR_EMAIL_ADDRESS` | 22 | Used in `project/views.py`, `project/forms.py` |
| `EMAIL_PROJECT_REVIEW_CONTACT` | 23 | Only used in a template mailto link, not a send call |
| `EMAIL_DEVELOPMENT_EMAIL_LIST` | 24 | DEBUG-mode recipient override |
| `EMAIL_OPT_OUT_INSTRUCTION_URL` | 25 | Included in every template context |
| `EMAIL_ALLOCATION_EXPIRING_NOTIFICATION_DAYS` | 26-28 | Default `[7, 14, 30]`; drives the expiry cron |
| `EMAIL_SIGNATURE` | 29 | Included in every template context |
| `EMAIL_ADMINS_ON_ALLOCATION_EXPIRE` | 30 | Gates the admin expiry digest |
| `EMAIL_ALLOCATION_EULA_REMINDERS` | 31 | Gates the weekly EULA reminder schedule |
| `EMAIL_ALLOCATION_EULA_IGNORE_OPT_OUT` | 32 | Used in `allocation/tasks.py`, `allocation/views.py` |
| `EMAIL_ALLOCATION_EULA_CONFIRMATIONS` | 33 | Gates EULA accept/decline confirmation emails |
| `EMAIL_ALLOCATION_EULA_CONFIRMATIONS_CC_MANAGERS` | 34-36 | CC managers on EULA confirmations |
| `EMAIL_ALLOCATION_EULA_INCLUDE_ACCEPTED_EULA` | 37 | Include EULA text in confirmation email |
| `EMAIL_DIRECTOR_PENDING_PROJECT_REVIEW_EMAIL` | `config/core.py:111` | Free-text director boilerplate; not wired to any send call found |
| `PROJECT_INSTITUTION_EMAIL_MAP` | `config/core.py:73,142` | Not mail-sending — maps institution domains to project codes |

**Known pre-existing quirk:** `allocation/tasks.py:248` passes `[EMAIL_ADMIN_LIST]` to
the admin-expiry digest send, but `EMAIL_ADMIN_LIST` is already a list — this wraps it
as a list-of-a-list. Worth resolving if this notification gets rerouted through a
ticket queue.

## 3. Email templates (`coldfront/templates/email/*.txt`, plain text)

| Template | Triggered by | Recipient |
|---|---|---|
| `new_allocation_request.txt` | New allocation request submitted | `EMAIL_TICKET_SYSTEM_ADDRESS` |
| `new_allocation_change_request.txt` | Allocation change request submitted | `EMAIL_TICKET_SYSTEM_ADDRESS` |
| `allocation_renewed.txt` | Allocation renewal processed | `EMAIL_TICKET_SYSTEM_ADDRESS` |
| `upgrade_account_request.txt` | User requests account upgrade to PI | `EMAIL_TICKET_SYSTEM_ADDRESS` |
| `allocation_activated.txt` | Allocation approved/activated | requestor/allocation users |
| `allocation_denied.txt` | Allocation denied | requestor |
| `allocation_revoked.txt` | Allocation revoked | requestor |
| `allocation_change_approved.txt` | Allocation change approved | requestor |
| `allocation_change_denied.txt` | Allocation change denied | requestor |
| `allocation_expiring.txt` | Allocation nearing expiration (cron) | allocation users |
| `allocation_expired.txt` | Allocation expired (cron) | allocation users |
| `admin_allocation_expired.txt` | Digest of expired allocations (cron, gated) | `EMAIL_ADMIN_LIST` |
| `allocation_agree_to_eula.txt` | User added to an allocation requiring EULA | allocation user |
| `allocation_eula_reminder.txt` | Weekly cron reminder for pending EULA | allocation user(s) |
| `allocation_eula_accepted.txt` | User accepts EULA | user (+ managers if CC enabled) |
| `allocation_eula_declined.txt` | User declines EULA | user (+ managers if CC enabled) |
| `user_added_to_allocation.txt` | User manually added to an allocation | added user |
| `user_added_to_project.txt` | User added to a project (with allocations) | added user |
| `new_project_review.txt` | PI submits annual project review | `EMAIL_DIRECTOR_EMAIL_ADDRESS` |
| `project_archived.txt` | Project archived | all project users |

Two related HTML templates render a *web form* for composing an email, not the email
body itself:
- `coldfront/core/project/templates/project/project_review_email.html` — form for
  `ProjectReviewEmailView`; `form_valid` (`project/views.py:1510`) sends free-text
  `send_email(...)` (not templated) to the PI, CC'd director/admins.
- `coldfront/core/allocation/templates/allocation/allocation_email_pending_request.html`
  — appears orphaned; no `.py` file references it.

## 4. Call sites by app

**`coldfront/core/user/`**
- `views.py:220-224` — `UserUpgradeAccount.post()` → `upgrade_account_request.txt` →
  `[EMAIL_TICKET_SYSTEM_ADDRESS]`. **Hits the ticket address directly today.**

**`coldfront/core/project/`**
- `models.py:325-329` — `Project.archive()` → `project_archived.txt` → all project users.
- `views.py:1024-1027` — user added to project → `user_added_to_project.txt` → new user.
- `views.py:1393-1397` — project review submitted → `new_project_review.txt` →
  `EMAIL_DIRECTOR_EMAIL_ADDRESS`.
- `views.py:1510-1516` — `ProjectReviewEmailView.form_valid` — ad-hoc `send_email()`
  (free text, not templated), from `EMAIL_DIRECTOR_EMAIL_ADDRESS` to the PI, CC list
  from form input (pre-filled from `EMAIL_DIRECTOR_EMAIL_ADDRESS` + `EMAIL_ADMIN_LIST`
  in `forms.py:15-16,118`).

**`coldfront/core/allocation/`**
- `tasks.py:71-77` — `send_eula_reminders()` (weekly cron) →
  `allocation_eula_reminder.txt` → users with pending EULA.
- `tasks.py:151-158` — `send_expiry_emails()` (daily cron) → `allocation_expiring.txt`
  → users nearing expiry (per `EMAIL_ALLOCATION_EXPIRING_NOTIFICATION_DAYS`).
- `tasks.py:227-234` — same task → `allocation_expired.txt` → users whose allocation
  expired.
- `tasks.py:244-249` — same task, gated by `EMAIL_ADMINS_ON_ALLOCATION_EXPIRE` →
  `admin_allocation_expired.txt` digest → `EMAIL_ADMIN_LIST` (see the double-list
  quirk above).
- `models.py:390-397` — `AllocationUser` add flow, EULA pending → ad-hoc
  `send_email_template` call inside the model (bypasses the `send_allocation_*`
  helpers) → `allocation_agree_to_eula.txt` → added user.
- `views.py:282-286` — allocation activated → `allocation_activated.txt` → customer.
- `views.py:302-307` — allocation denied → `allocation_denied.txt` → customer.
- `views.py:310-315` — allocation revoked → `allocation_revoked.txt` → customer.
- `views.py:406-411` — EULA accepted → `allocation_eula_accepted.txt` (gated by
  `EMAIL_ALLOCATION_EULA_CONFIRMATIONS`, CC managers optional).
- `views.py:426-431` — EULA declined → `allocation_eula_declined.txt` (same gating).
- `views.py:695-700` — new allocation request → `send_allocation_admin_email` →
  `new_allocation_request.txt` → `EMAIL_TICKET_SYSTEM_ADDRESS`. **Ticket-routed.**
- `views.py:824-829` — user added to allocation → `user_added_to_allocation.txt` →
  added user.
- `views.py:1239-1244` — allocation renewed → `send_allocation_admin_email` →
  `allocation_renewed.txt` → `EMAIL_TICKET_SYSTEM_ADDRESS`. **Ticket-routed.**
- `views.py:1710-1715` — allocation change denied → `allocation_change_denied.txt` →
  customer.
- `views.py:1801-1807` — allocation change approved →
  `allocation_change_approved.txt` → customer.
- `views.py:2005-2010` — new allocation change request → `send_allocation_admin_email`
  → `new_allocation_change_request.txt` → `EMAIL_TICKET_SYSTEM_ADDRESS`.
  **Ticket-routed.**

## 5. Existing ticket-system references

There is no integration with a named external ticketing product — no hits anywhere in
`coldfront/` (case-insensitive) for "helpdesk", "issue tracker", "zendesk", "jira",
"servicenow", "freshdesk", or "footprints". What already exists is a generic mailbox
seam:

- `config/email.py:21` — `EMAIL_TICKET_SYSTEM_ADDRESS`, an env-configurable address
  presumably meant to be a ticketing system's email-to-ticket intake address.
- `utils/mail.py:92-99` (`send_admin_email_template`) and `:102-120`
  (`send_allocation_admin_email`) route to it.
- `user/views.py:220-224` (account upgrade request) sends to it directly.
- `config/core.py:125-128` — `ACCOUNT_CREATION_TEXT` mentions "submit a help ticket to
  request an account" — UI copy only, no functional link to a ticket system.

**This is the natural seam to extend:** either point `EMAIL_TICKET_SYSTEM_ADDRESS` at
the new system's intake address (if it accepts email-to-ticket), or add a parallel
helper next to `send_admin_email_template` that calls the ticketing system's API
instead of/in addition to sending mail.

## 6. Periodic/cron tasks (django-q2)

Registered in `coldfront/core/utils/management/commands/add_scheduled_tasks.py`:
- Line 24 — `allocation.tasks.update_statuses`, daily (expires allocations past
  `end_date`; no email itself, but its status changes are what later trigger
  `send_expiry_emails`).
- Line 26 — `allocation.tasks.send_expiry_emails`, daily — drives the expiring-soon,
  expired, and admin-digest emails above.
- Lines 28-31 — `allocation.tasks.send_eula_reminders`, weekly, only when
  `ALLOCATION_EULA_ENABLE and EMAIL_ALLOCATION_EULA_REMINDERS`.

No other scheduled email-sending tasks exist in the codebase.

## 7. Plugin-specific emailing

None. Checked every app under `coldfront/plugins/` (`system_monitor`, `slurm`,
`freeipa`, `ldap_user_search`, `project_openldap`, `auto_compute_allocation`,
`mokey_oidc`, `xdmod`, `iquota`, `api`) — no plugin sends email. The only
mail-adjacent hits are LDAP/FreeIPA/OIDC syncing a user's `email` *field* (not sending
messages), and `plugins/freeipa/management/commands/freeipa_expire_users.py:16`, which
imports `build_link` purely to construct a URL string for a text report row.

## Where to hook in a ticketing integration

In priority order:

1. **`send_admin_email_template` / `send_allocation_admin_email`**
   (`coldfront/core/utils/mail.py:92`, `:102`) — the sole existing channel that already
   targets `EMAIL_TICKET_SYSTEM_ADDRESS`. Covers new allocation requests, allocation
   renewals, and new allocation change requests (`allocation/views.py:695`, `:1239`,
   `:2005`).
2. **`coldfront/core/user/views.py:220-224`** — account-upgrade request, also
   ticket-address-bound today.
3. For broader coverage (routing customer-facing notifications into tickets too), wrap
   `send_email_template()` itself (`mail.py:57`), since nearly every other call site
   funnels through it.
