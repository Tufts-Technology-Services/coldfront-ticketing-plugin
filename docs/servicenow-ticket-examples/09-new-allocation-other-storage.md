# New allocation: other storage (Tier 3 Research)

When: the storage plugin has created the share for a new Tier 3 Research allocation
(`share_creation_successful`).

ColdFront resolves the [allocation request](04-allocation-request.md)'s ticket with this message, so
it goes to whoever is on that ticket: here the PI as contact person and the Approver on the watch
list.

The general storage message, for storage without a message of its own: Tier 3 Research, TTS Staff - Tier 3 Research (without the charge-back paragraph) and Tier 1 NFS Rstore. It has no access guide, and lists the path if the allocation has a `Storage Path`.

<!-- Generated below this line by ticketing/tests/test_ticket_examples.py. To refresh: UPDATE_TICKET_EXAMPLES=1 uv run pytest ticketing/tests/test_ticket_examples.py -->

### Resolved

| Field | Value |
|---|---|
| Assigned to | svc_rcrm |
| State | Resolved (6) |
| Close code | Solved Remotely (Permanently) |
| Close notes | Allocation activated. |

**Additional comments (customer-visible: ServiceNow emails the contact person and watch list)**

```text
Hello,

The Tier 3 Research storage allocation for project "Smith Lab" has been created.

Quota: 50 TB

View this allocation in RCRM: https://rcrm.it.tufts.edu/allocation/1/

Research Computing Resource Manager (RCRM): The RCRM is now available at https://rcrm.it.tufts.edu/, providing owners and approvers with self-service tools to manage user access, request research storage quota adjustment, and update billing information for tiered research storage charge-backs. Please see the Research Technology Guides RCRM for detailed instructions. https://rtguides.it.tufts.edu/storage/rcrm/index.html

NOTE: Only RT can access and move data to/from Tier 3. 

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
