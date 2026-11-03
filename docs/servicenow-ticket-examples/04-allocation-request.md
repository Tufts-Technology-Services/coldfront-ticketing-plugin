# Allocation request

When: someone requests a new allocation in ColdFront (`allocation_new`), for any resource.

This is the admins' ticket for the request. It waits New in the queue; the description is for
admins. When the allocation is ready, ColdFront resolves this same ticket with the message for its
resource (see the "New allocation" pages). In this example the PI asked for Tier 1 HPC storage, so
the Approver is watch-listed.

If an admin with ticketing rights submits a request, nothing opens yet; the ticket is filed resolved
when the allocation is activated, assigned to whoever activated it.

### Opened

Opened with no state, so ServiceNow starts it New in the group's queue.

| Field | Value |
|---|---|
| Short description | Tier 1 HPC: New Allocation Request \| 10 TB \| Smith Lab |
| Caller (not emailed) | asmith02 |
| Contact person (emailed) | asmith02 |
| Watch list (emailed) | bjones03 |
| Source (contact_type) | self-service |
| Business service | Research Data Storage |
| Action | Activate/Setup/Install |
| Category | Research |
| Subcategory | Research Systems |
| Assignment group | TTS Research Technology |
| State | New (1) |

**Description (internal, not emailed)**

```text
New allocation requested in ColdFront.

Project:            Smith Lab
PI:                 Alex Smith (asmith02)
Requested by:       Alex Smith (asmith02)
Resource:           Tier 1 HPC
Storage Quota (TB): 10

Reason for request:
Sequencing data for the 2026-27 NIH R01 project.

Review in ColdFront: https://rcrm.it.tufts.edu/allocation/1/
```

Nothing customer-visible: no one is emailed.
