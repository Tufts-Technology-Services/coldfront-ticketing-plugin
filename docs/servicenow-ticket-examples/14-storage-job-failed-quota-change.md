# Storage job failed: quota change (admins only)

When: the storage plugin fails to apply an approved quota change (`quota_update_failed`).

As for a [failed new share](13-storage-job-failed-new-share.md), but a child of the
[quota change request](10-quota-change-request.md)'s ticket, with the admin who approved the change
as caller and contact person.

<!-- Generated below this line by ticketing/tests/test_ticket_examples.py. To refresh: UPDATE_TICKET_EXAMPLES=1 uv run pytest ticketing/tests/test_ticket_examples.py -->

### Opened

Opened with no state, so ServiceNow starts it New in the group's queue.

| Field | Value |
|---|---|
| Short description | Tier 1 RStore: Storage Job Failed \| Smith Lab |
| Caller (not emailed) | privera04 |
| Contact person (emailed) | privera04 |
| Source (contact_type) | monitoring |
| Business service | Research Data Storage |
| Action | Move/Add/Change |
| Category | Research |
| Subcategory | Research Systems |
| Assignment group | TTS Research Technology |
| Parent incident | INC0000001 |
| State | New (1) |

**Description (internal, not emailed)**

```text
A storage job failed after approval in RCRM. The customer hasn't been told.

Job:          Quota change
State:        failed: quota below current usage
Project:      Smith Lab
Owner:        Alex Smith (asmith02)
Resource:     Tier 1 RStore
Path:         smb://rstore.it.tufts.edu/smithlab$
Quota:        8 TB
Approved by:  privera04
Request:      INC0000001

When the job succeeds, RCRM resolves the request's ticket and this one.

In RCRM: https://rcrm.it.tufts.edu/allocation/change-request/1/
```

Nothing customer-visible: no one is emailed.
