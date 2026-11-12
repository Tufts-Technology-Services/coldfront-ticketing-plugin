# New allocation: Tier 1 HPC

When: the storage plugin has created the share for a new Tier 1 HPC allocation
(`share_creation_successful`).

ColdFront resolves the [allocation request](04-allocation-request.md)'s ticket with this message, so
it goes to whoever is on that ticket: here the PI as contact person and the Approver on the watch
list.

Also sent for Tier 1 Other, and for TTS Staff - Tier 1 HPC without the charge-back paragraph.

<!-- Generated below this line by ticketing/tests/test_ticket_examples.py. To refresh: UPDATE_TICKET_EXAMPLES=1 uv run pytest ticketing/tests/test_ticket_examples.py -->

### Resolved

| Field | Value |
|---|---|
| Assigned to | privera04 |
| State | Resolved (6) |
| Close code | Solved Remotely (Permanently) |
| Close notes | Allocation activated. |

**Additional comments (customer-visible: ServiceNow emails the contact person and watch list)**

```text
Hello,

The Tier 1 HPC storage allocation for project "Smith Lab" has been created. Please reference the self-service guidance below for storage access. NOTE: It may take 24 hrs for your Tufts credentials to propagate, before you can access the new storage.

Path:  /cluster/tufts/smithlab
Quota: 10 TB

View this allocation in RCRM: https://rcrm.it.tufts.edu/allocation/1/

Tiered Storage Allocation Self-Service Access Guides
Tier 1 HPC (https://rtguides.it.tufts.edu/storage/systems/10-tier1-hpc.html#access-methods)

Research Computing Resource Manager (RCRM): The RCRM is now available at https://rcrm.it.tufts.edu/, providing owners and approvers with self-service tools to manage user access, request research storage quota adjustment, and update billing information for tiered research storage charge-backs. Please see the Research Technology Guides RCRM for detailed instructions. https://rtguides.it.tufts.edu/storage/rcrm/index.html

Interested in a quick demo? Go to the link below to schedule an RCRM consultation to learn how to make the most of these self-service features.
https://outlook.office.com/book/RDMSSupport@tufts.edu

Tiered Research Storage Charge-Back: Storage owners and approvers should manage research storage resources, plan and budget accordingly for any potential storage charges. For complete details about the charge-back model, including frequently asked questions, please visit the resources below:

https://it.tufts.edu/research-data-storage#changes
https://it.tufts.edu/research-data-storage-faq

Please visit the TTS Research Technology Guides (https://rtguides.it.tufts.edu/) or email TTS-Research@tufts.edu for additional support.

Thank you,

Tufts Technology Services (TTS)
Research Technology (RT)
it.tufts.edu/research-technology
```
