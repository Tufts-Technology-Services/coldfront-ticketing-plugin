# Quota change request

When: someone submits a change request for an allocation (`allocation_change_created`).

The admins' ticket for the request; it waits New in the queue. When the change is made, ColdFront
resolves this same ticket with the [quota change made](11-quota-change-made-tier-1-hpc.md) message.
In this example the Approver asked, so the PI is watch-listed.

<!-- Generated below this line by ticketing/tests/test_ticket_examples.py. To refresh: UPDATE_TICKET_EXAMPLES=1 uv run pytest ticketing/tests/test_ticket_examples.py -->

### Opened

Opened with no state, so ServiceNow starts it New in the group's queue.

| Field | Value |
|---|---|
| Short description | Tier 1 HPC: Quota Change Request \| 20 TB \| smithlab |
| Caller (not emailed) | asmith02 |
| Contact person (emailed) | bjones03 |
| Watch list (emailed) | asmith02 |
| Source (contact_type) | self-service |
| Business service | Research Data Storage |
| Action | Move/Add/Change |
| Category | Research |
| Subcategory | Research Systems |
| Assignment group | TTS Research Technology |
| State | New (1) |

**Description (internal, not emailed)**

```text
Allocation change requested in RCRM.

Project:      Smith Lab
Owner:        Alex Smith (asmith02)
Requested by: Bea Jones (bjones03)
Resource:     Tier 1 HPC
Path:         /cluster/tufts/smithlab

Requested changes:
- Storage Quota (TB): 10 -> 20

Reason for change:
New imaging instrument doubles our data rate this year.

Review in RCRM: https://rcrm.it.tufts.edu/allocation/change-request/1/
```

Nothing customer-visible: no one is emailed.
