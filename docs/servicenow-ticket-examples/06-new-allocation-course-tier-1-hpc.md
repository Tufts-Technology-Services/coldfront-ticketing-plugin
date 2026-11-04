# New allocation: Course - Tier 1 HPC

When: the storage plugin has created the share for a new Course - Tier 1 HPC allocation
(`share_creation_successful`).

ColdFront resolves the [allocation request](04-allocation-request.md)'s ticket with this message, so
it goes to whoever is on that ticket: here the PI as contact person and the Approver on the watch
list.

The groups are the allocation's `Group` attribute (else the project's) and that name plus `admin`, as the storage plugin creates them. Course storage isn't charged for, so there's no charge-back paragraph.

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

The Tier 1 HPC class storage for "BIO 101 Fall 2026" has been created. Please reference the self-service guidance below for storage access. NOTE: It may take 24 hrs for your Tufts credentials to propagate, before you can access the new storage.

Path:  /cluster/tufts/bio101
Quota: 1 TB

View this allocation in RCRM: https://rcrm.it.tufts.edu/allocation/1/

The permission setting for class folders is unique. There are two groups for the class: an admin group (bio101admin), which only includes instructors and TAs, and a general class group (bio101), which includes everyone in the class.

This setup controls folder access as follows:
- Students can't read each other's folders.
- Students have access to their own folder and the shared folder (/cluster/tufts/bio101/shared).
- Please store the files you need to share with all students (instructions, demo files, sample scripts, reference data, etc.) in the shared folder.
- Instructors and TAs have access to all folders: their own, the students', and the shared folder.

You and your TAs can now start working in the class folder to prepare for the new semester.

Tiered Storage Allocation Self-Service Access Guides
Tier 1 HPC (https://rtguides.it.tufts.edu/storage/systems/10-tier1-hpc.html#access-methods)

Research Computing Resource Manager (RCRM): The RCRM is now available at https://rcrm.it.tufts.edu/, providing owners and approvers with self-service tools to manage user access, request research storage quota adjustment, and update billing information for tiered research storage charge-backs. Please see the Research Technology Guides RCRM for detailed instructions. https://rtguides.it.tufts.edu/storage/rcrm/index.html

NOTE: Only RT can access and move data to/from Tier 3. 

Interested in a quick demo? Go to the link below to schedule an RCRM consultation to learn how to make the most of these self-service features.
https://outlook.office.com/book/RDMSSupport@tufts.edu

Please visit the TTS Research Technology Guides (https://rtguides.it.tufts.edu/) or email TTS-Research@tufts.edu for additional support.

Thank you,

Tufts Technology Services (TTS)
Research Technology (RT)
it.tufts.edu/research-technology
```
