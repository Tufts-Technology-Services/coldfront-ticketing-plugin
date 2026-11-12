# ServiceNow tickets ColdFront files: examples

These are the ServiceNow incidents ColdFront (RCRM) files, with an example of each, so you can see
what goes into the queue and what customers are emailed. They were made by running the plugin's
real code against a stand-in for ServiceNow, so the fields and text are exactly what it sends. The
people, projects, paths and incident numbers are made up.

All tickets go to the **TTS Research Technology** assignment group, category Research /
Research Systems.

## How to read a page

Each page says when the ticket is filed, then shows each step of its life: the fields set, and the
text.

- **Description** and **work notes** are internal. Only people working the ticket see them.
- **Additional comments** are customer-visible. ServiceNow emails them to the contact person and
  everyone on the watch list. It doesn't email the caller, so anyone who must hear is the contact
  person or a watcher.
- The **caller** (Affected Client) is the project's PI on every allocation and project ticket,
  whoever asked. The PI is also on the watch list unless they're the contact person, so they still
  hear. The admin-only failure tickets are the exception: their caller is the admin.
- A ticket **opened** by ColdFront has no assignee: it waits New in the queue for someone to pick it
  up. A ticket **filed resolved** is a notice, created already resolved so that ServiceNow emails
  the message.
- **Who a resolved ticket is assigned to.** A ticket someone picked up keeps them. Otherwise:
  - an allocation request or change request goes to the admin who activated or approved it, and a
    storage failure ticket to the admin who approved its request;
  - a project user notice goes to whoever changed the project's users if they're an RCRM admin, and
    is otherwise left unassigned (shown as "no one");
  - a duplicate ColdFront closes when a ServiceNow ticket is linked goes to the admin who linked it;
  - anything else goes to the ColdFront service account, the `TICKETING_ASSIGNED_TO` setting (shown
    as `svc_rcrm (TICKETING_ASSIGNED_TO)`): HPC account tickets, and the cases above when ColdFront
    can't tell who the admin was or ServiceNow doesn't know them.

## People in the examples

| Username | Who | Role |
|---|---|---|
| asmith02 | Alex Smith | PI of "Smith Lab" (and of the course "BIO 101 Fall 2026") |
| bjones03 | Bea Jones | Approver on the project (ColdFront role Manager) |
| jdoe01 | Jordan Doe | Signs up for an HPC account |
| slee05 | Sam Lee | Added to the project |
| privera04 | Pat Rivera | RT admin who activates allocations and approves changes |
| svc_rcrm | | ColdFront's ServiceNow service account, the `TICKETING_ASSIGNED_TO` setting |

## The tickets

**HPC accounts**

| Page | Filed when | Who is emailed |
|---|---|---|
| [HPC account created](01-hpc-account-created.md) | An account is set up (the usual case) | The user |
| [HPC account request that has to wait](02-hpc-account-request-waiting.md) | An account has to wait for a UID; resolved when it is set up | The user |
| [HPC account setup failed](03-hpc-account-setup-failed.md) | Setting up an account fails | No one (admins only) |

**Storage allocations**

| Page | Filed when | Who is emailed |
|---|---|---|
| [Allocation request](04-allocation-request.md) | Someone requests an allocation (opens in the queue) | No one yet |
| [New allocation: Tier 1 HPC](05-new-allocation-tier-1-hpc.md) | The share is created; resolves the request | Requester, PI and Approvers |
| [New allocation: Course - Tier 1 HPC](06-new-allocation-course-tier-1-hpc.md) | The same, for class storage | Requester, PI and Approvers |
| [New allocation: Tier 1 RStore](07-new-allocation-tier-1-rstore.md) | The same | Requester, PI and Approvers |
| [New allocation: Tier 2 Research](08-new-allocation-tier-2-research.md) | The same | Requester, PI and Approvers |
| [New allocation: other storage](09-new-allocation-other-storage.md) | The same, for Tier 3 and NFS RStore | Requester, PI and Approvers |
| [Quota change request](10-quota-change-request.md) | Someone requests a quota change (opens in the queue) | No one yet |
| [Quota change made: Tier 1 HPC](11-quota-change-made-tier-1-hpc.md) | The quota is applied; resolves the request | Requester, PI and Approvers |
| [Quota change made: Tier 1 RStore](12-quota-change-made-tier-1-rstore.md) | The same | Requester, PI and Approvers |
| [Storage job failed: new share](13-storage-job-failed-new-share.md) | Creating the share fails | No one (admins only) |
| [Storage job failed: quota change](14-storage-job-failed-quota-change.md) | Applying the quota fails | No one (admins only) |

The TTS Staff and Course copies of each tier get that tier's message, without the charge-back
paragraph, since they aren't charged for. Each page says which resources share its message.

**Project membership**

| Page | Filed when | Who is emailed |
|---|---|---|
| [Project users changed](15-project-users-changed.md) | Someone is added to or removed from a project (at most once a day per project) | Whoever made the change, PI and Approvers |
| [Added to a project](16-added-to-project.md) | Someone is added to a project | The person added |

## Not shown

- **The generic messages** for resources that aren't storage: every Tufts resource is storage
  today, so none are sent.

## Keeping these current

Below the marker comment on each page, the fields and text are generated by
`ticketing/tests/test_ticket_examples.py`, which runs the plugin's tasks with Tufts' settings. The
prose above the marker is written by hand. The test suite fails when a page no longer matches what
the plugin sends, so after changing a template or what a ticket carries, refresh the pages and
review the diff:

```sh
UPDATE_TICKET_EXAMPLES=1 uv run pytest ticketing/tests/test_ticket_examples.py
```

A new page needs a scenario in that module; the suite also fails on a page without one. The
refresh then creates the page, with placeholder prose above the marker to replace.
