# HPC account setup failed (admins only)

When: setting up an HPC account fails (`hpc_account_provisioning_failed`).

Only admins are on this ticket; the user isn't emailed. It is a child of the account's own ticket
when there is one (as in this example, where the account was waiting for a UID). A repeat failure
adds a work note instead of another ticket, and a later success resolves it.

### Opened

Opened with no state, so ServiceNow starts it New in the group's queue.

| Field | Value |
|---|---|
| Short description | HPC Account Setup Failed \| jdoe01 |
| Caller (not emailed) | svc_rcrm |
| Contact person (emailed) | svc_rcrm |
| Source (contact_type) | monitoring |
| Business service | High Performance Computing (HPC) |
| Action | Activate/Setup/Install |
| Category | Research |
| Subcategory | Research Systems |
| Assignment group | TTS Research Technology |
| Parent incident | INC0000001 |
| State | New (1) |

**Description (internal, not emailed)**

```text
Setting up an HPC account failed in ColdFront. The user hasn't been told.

User:     Jordan Doe (jdoe01)
Email:    jordan.doe@tufts.edu
Reason:   Could not create home directory /cluster/home/jdoe01: permission denied
Request:  INC0000001

Account creation doesn't retry a failure. Fix the cause, then run it again, from a ColdFront shell:

    from account_signup.tasks import provision_hpc_account
    provision_hpc_account("jdoe01")

When it succeeds, ColdFront welcomes the user and resolves this ticket.
```

Nothing customer-visible: no one is emailed.

### On a repeat failure

**Work note (internal, not emailed)**

```text
Setting up the HPC account failed again: Could not create home directory /cluster/home/jdoe01: permission denied
```

### Resolved when a later attempt succeeds

| Field | Value |
|---|---|
| Assigned to | svc_rcrm |
| State | Resolved (6) |
| Close code | Solved Remotely (Permanently) |
| Close notes | Setting up the HPC account succeeded on a later attempt. |
