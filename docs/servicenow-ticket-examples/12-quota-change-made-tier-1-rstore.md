# Quota change made: Tier 1 RStore

When: the storage plugin has applied an approved quota change (`quota_update_successful`).

ColdFront resolves the [quota change request](10-quota-change-request.md)'s ticket with this
message: here the Approver who asked is the contact person, and the PI is on the watch list.

The same message as for [Tier 1 HPC](11-quota-change-made-tier-1-hpc.md), naming the RStore resource.

<!-- Generated below this line by ticketing/tests/test_ticket_examples.py. To refresh: UPDATE_TICKET_EXAMPLES=1 uv run pytest ticketing/tests/test_ticket_examples.py -->

### Resolved

| Field | Value |
|---|---|
| Assigned to | privera04 |
| State | Resolved (6) |
| Close code | Solved Remotely (Permanently) |
| Close notes | Quota updated. |

**Additional comments (customer-visible: ServiceNow emails the contact person and watch list)**

```text
Hello,

The Tier 1 RStore storage allocation for project "Smith Lab" has been adjusted from 5 TB to 8 TB.

View this allocation in RCRM: https://rcrm.it.tufts.edu/allocation/1/

Research Computing Resource Manager (RCRM): The RCRM is now available at https://rcrm.it.tufts.edu/, providing owners and approvers with self-service tools to manage user access, request research storage quota adjustment, and update billing information for tiered research storage charge-backs. Please see the Research Technology Guides RCRM for detailed instructions. https://rtguides.it.tufts.edu/storage/rcrm/index.html

Interested in a quick demo? Go to the link below to schedule an RCRM consultation to learn how to make the most of these self-service features.
https://outlook.office.com/book/RDMSSupport@tufts.edu

Tiered Research Storage Charge-Back: Storage owners and approvers should manage research storage resources, plan and budget accordingly for any potential storage charges. For complete details about the charge-back model, including frequently asked questions, please visit the resources below:

https://it.tufts.edu/research-data-storage#changes
https://it.tufts.edu/research-data-storage-faq

Please email TTS-Research@tufts.edu for additional support.

Thank you,

Tufts Technology Services (TTS)
Research Technology (RT)
it.tufts.edu/research-technology
```
