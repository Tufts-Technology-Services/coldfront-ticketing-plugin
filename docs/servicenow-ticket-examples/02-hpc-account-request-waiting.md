# HPC account request that has to wait

When: someone signs up for an HPC account that can't be set up straight away, because it is
waiting for a UID (`hpc_account_requested`).

A ticket opens in the queue and the user is told there will be a wait. When the account is set up,
ColdFront resolves that ticket with the same welcome as
[HPC account created](01-hpc-account-created.md).

<!-- Generated below this line by ticketing/tests/test_ticket_examples.py. To refresh: UPDATE_TICKET_EXAMPLES=1 uv run pytest ticketing/tests/test_ticket_examples.py -->

### Opened

Opened with no state, so ServiceNow starts it New in the group's queue.

| Field | Value |
|---|---|
| Short description | HPC Account Request |
| Caller (not emailed) | jdoe01 |
| Contact person (emailed) | jdoe01 |
| Source (contact_type) | self-service |
| Business service | High Performance Computing (HPC) |
| Action | Activate/Setup/Install |
| Category | Research |
| Subcategory | Research Systems |
| Assignment group | TTS Research Technology |
| State | New (1) |

**Description and additional comments (the same text; the comment is emailed to the contact person)**

```text
Hello,

We have received your request for an HPC account. Setting up a new account takes some time, so
there will be a wait before you can use it. You will receive a follow-up message once your account
has been provisioned.

Thank you,

Tufts Technology Services (TTS)
Research Technology (RT)
it.tufts.edu/research-technology
```

### Resolved when the account is set up

| Field | Value |
|---|---|
| Assigned to | svc_rcrm (`TICKETING_ASSIGNED_TO`) |
| State | Resolved (6) |
| Close code | Solved Remotely (Permanently) |
| Close notes | Cluster account created! |

**Additional comments (customer-visible: ServiceNow emails the contact person and watch list)**

```text
Hello,

WELCOME
to the Tufts HPC cluster provided by Tufts Technology Services.
Please go to the link below to review IMPORTANT information on:
Cluster Access, User Guides, Storage, and User Support.
https://go.tufts.edu/cluster
 
Tufts Network -- 2FA
2FA via DUO is required before you can utilize the Tufts HPC Cluster when work on Tufts network.
Details about 2FA on Tufts HPC cluster can be found at:
https://tufts.app.box.com/file/835895377130?v=2FAonTuftsHPC
If assistance is needed with Tufts 2FA, please contact TTS Service Desk at 617-627-3376.
 
Outside Tufts Network -- VPN
Tufts VPN (https://access.tufts.edu/vpn) is required before you can utilize the Tufts HPC Cluster and OnDemand web interface when work outside Tufts network.
If assistance is needed with Tufts VPN, please contact TTS Service Desk at 617-627-3376.
 
New User Guides
New users of the Tufts HPC cluster should utilize the support documents within the links below.
The contents are comprised of the following information: login, file transfer, job submission, using applications/software, updated workshop information, etc.
HPC User Guide (https://go.tufts.edu/cluster)

Thank you,

Tufts Technology Services (TTS)
Research Technology (RT)
it.tufts.edu/research-technology
```
