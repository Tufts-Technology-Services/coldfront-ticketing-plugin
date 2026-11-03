# Added to a project (the new member)

When: someone is added to a project (`project_activate_user`). Sent as well as the
[project users changed](15-project-users-changed.md) notice.

One per person, project and day, filed already resolved. The person added is the contact person,
so ServiceNow emails them; the PI is the caller, as on every project ticket; no one is watch-listed.
Whoever added them isn't on the ticket; they only decide the source (self-service when it's the PI or
an Approver).
Nothing is sent to people who are removed, to the PI, or to ticketing admins.

### Filed resolved

| Field | Value |
|---|---|
| Short description | Added to Project \| Smith Lab |
| Caller (not emailed) | asmith02 |
| Contact person (emailed) | slee05 |
| Source (contact_type) | self-service |
| Business service | Research Data Storage |
| Action | Move/Add/Change |
| Category | Research |
| Subcategory | Research Systems |
| Assignment group | TTS Research Technology |
| Assigned to | svc_rcrm |
| State | Resolved (6) |
| Close code | Solved Remotely (Permanently) |
| Close notes | User added to project. |

**Additional comments (customer-visible: ServiceNow emails the contact person and watch list)**

```text
Hello,

You have been added to project "Smith Lab". Please reference the self-service guide below for storage access. NOTE: It may take 1-2 hrs for your Tufts credentials to propagate, before you can access the storage.

Tiered Storage Allocation Self-Service Access Guides
Tier 1 HPC (https://rtguides.it.tufts.edu/storage/systems/10-tier1-hpc.html#access-methods)
Tier 1 RStore (https://rtguides.it.tufts.edu/storage/systems/20-tier1-rstore.html#access-methods)
Tier 2 Research (https://rtguides.it.tufts.edu/storage/systems/30-tier2.html#access-method-globus)

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
