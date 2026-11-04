# Storage job failed: new share (admins only)

When: the storage plugin fails to create a share after an allocation is activated
(`share_creation_failed`).

Only admins are on this ticket; the customer isn't emailed, and the request's ticket stays open. It
is a child of the [allocation request](04-allocation-request.md)'s ticket, with the admin who
activated the allocation as caller and contact person: unlike the customer tickets, the PI isn't the
caller, so the ticket stays out of their view. A repeat failure adds a work note, and a later success resolves
it (and the request's ticket, with the customer's message).

<!-- Generated below this line by ticketing/tests/test_ticket_examples.py. To refresh: UPDATE_TICKET_EXAMPLES=1 uv run pytest ticketing/tests/test_ticket_examples.py -->

### Opened

Opened with no state, so ServiceNow starts it New in the group's queue.

| Field | Value |
|---|---|
| Short description | Tier 1 HPC: Storage Job Failed \| Smith Lab |
| Caller (not emailed) | privera04 |
| Contact person (emailed) | privera04 |
| Source (contact_type) | monitoring |
| Business service | Research Data Storage |
| Action | Activate/Setup/Install |
| Category | Research |
| Subcategory | Research Systems |
| Assignment group | TTS Research Technology |
| Parent incident | INC0000001 |
| State | New (1) |

**Description (internal, not emailed)**

```text
A storage job failed after approval in RCRM. The customer hasn't been told.

Job:          Share creation
State:        failed: VAST API timed out creating /cluster/tufts/smithlab
Project:      Smith Lab
Owner:        Alex Smith (asmith02)
Resource:     Tier 1 HPC
Path:         /cluster/tufts/smithlab
Quota:        10 TB
Approved by:  privera04
Request:      INC0000001

When the job succeeds, RCRM resolves the request's ticket and this one.

In RCRM: https://rcrm.it.tufts.edu/allocation/1/
```

Nothing customer-visible: no one is emailed.

### On a repeat failure

**Work note (internal, not emailed)**

```text
Share creation failed again: failed: VAST API timed out creating /cluster/tufts/smithlab
```

### Resolved when the job succeeds

| Field | Value |
|---|---|
| Assigned to | svc_rcrm |
| State | Resolved (6) |
| Close code | Solved Remotely (Permanently) |
| Close notes | Share creation succeeded on a later attempt. |
