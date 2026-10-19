"""What allocation tickets say, by what kind of allocation it is.

Two lifecycles, each with two moments:

    new allocation   describe_new_allocation()  opened on request   admin-facing, every allocation
                     describe_activation()      resolved           customer-facing, every allocation
    change request   describe_request()         opened on request   admin-facing, every change
                     describe_change()          resolved           customer-facing, every change

Resolved when the storage plugin reports success for what it provisions, else on approval. The
same test, storage_will_provision, picks the customer message: the storage plugin's work (a new
share; a quota change) gets the storage messages, and everything else the generic ones. The message
is the first template that exists of

    ticketing/<folder>/<resource name>.txt
    ticketing/<folder>/<resource type>.txt
    ticketing/<folder>/generic.txt

(names slugified). Folders: allocation_new/ for activations, allocation_update/ for quota changes and
allocation_change/ for every other change, so a Storage change with no quota change never picks up
allocation_update/storage.txt, which is about the quota. A resource that needs its own wording --
e.g. HPC vs RStore storage, or a cluster -- gets a template named after it or its type. Only an
allocation with no resource has no message (UnsupportedUpdate).
"""

import re
from dataclasses import dataclass

from coldfront.core.utils.mail import build_link
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.text import capfirst, slugify

from ticketing.tickets import (
    DEFAULT_BUSINESS_SERVICE,
    U_ACTION_ACTIVATE,
    U_ACTION_MOVE_ADD_CHANGE,
    clean_short_description,
)

# The one supported update today. Matches the storage plugin's QUOTA_ATTRIBUTE_NAME default.
QUOTA_ATTRIBUTE_NAME = "Storage Quota (TB)"
QUOTA_UNITS = "TB"
PATH_ATTRIBUTE_NAME = "Storage Path"


# ServiceNow business service per resource type; types not listed keep the ServiceNowIncident default (HPC).
BUSINESS_SERVICE_BY_RESOURCE_TYPE = {
    "Storage": "Research Data Storage",
}


# Where camelCase starts a new word: a lower-case letter or digit then a capital ("slurmAccount"),
# or the last capital of a run that starts a capitalized word ("QOSExpire" -> "QOS", "Expire").
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_MACHINE_NAME = re.compile(r"[_-]|[a-z0-9][A-Z]|[A-Z][A-Z][a-z]")

# Words written a particular way in ticket text, matched whole and ignoring case. Nothing
# site-specific: sites add their own (Tufts: UTLN, RStore, ...), or respell one of these, with the
# TICKETING_KNOWN_TERMS setting.
KNOWN_TERMS = (
    # identifiers
    "ID", "UID", "GID", "PI",
    # computing
    "HPC", "CPU", "GPU", "RAM", "QOS", "VM", "OS", "SSD",
    # storage and network
    "NFS", "SMB", "CIFS", "POSIX", "VAST", "IP", "DNS", "URL", "API", "VPN", "SSH",
    # accounts and security
    "LDAP", "MFA", "2FA", "EULA",
    # units
    "KB", "MB", "GB", "TB", "PB",
    # products and services, as they write themselves
    "FreeIPA", "Slurm", "ColdFront", "Grouper",
)  # fmt: skip


def known_terms() -> dict[str, str]:
    """Lower-cased term -> how to write it: KNOWN_TERMS, then TICKETING_KNOWN_TERMS, which adds terms
    and respells defaults. Read on each call, so the setting can be overridden in tests."""
    extra = getattr(settings, "TICKETING_KNOWN_TERMS", ())
    return {term.lower(): term for term in (*KNOWN_TERMS, *extra)}


def human_name(name: str) -> str:
    """An attribute name as people would write it, for ticket text.

    snake_case, kebab-case and camelCase become words with the first capitalized
    ("slurm_account_name" and "slurmAccountName" -> "Slurm account name"). Known terms are written
    their own way ("freeipa_group" -> "FreeIPA group", "share_creation_task_id" -> "Share creation
    task ID"; see known_terms). Other words in capitals stay so ("unix_XYZ" -> "Unix XYZ"), unless
    the whole name is in capitals ("CLOUD_USAGE_NOTIFICATION" -> "Cloud usage notification"). A
    name with spaces, or with nothing to split, is left as it is ("Storage Quota (TB)").
    """
    if " " in name or not _MACHINE_NAME.search(name):
        return name
    words = _CAMEL_BOUNDARY.sub(" ", name.replace("_", " ").replace("-", " ")).split()
    if not words:
        return name
    shouting = name.isupper()
    terms = known_terms()
    words = [terms.get(w.lower()) or (w if w.isupper() and not shouting else w.lower()) for w in words]
    return capfirst(" ".join(words))


class UnsupportedUpdate(Exception):
    """The allocation or change isn't one this plugin writes a customer message for: today, only an
    allocation with no resource."""


def storage_will_provision(allocation, change=None) -> bool:
    """Whether the storage plugin acts on this approval and records an outcome to resolve on.

    If it does, the request is resolved when the plugin records success (ticketing.signals);
    otherwise it is resolved on approval. Copies the plugin's own conditions (storage/signals.py,
    see "What the storage plugin doesn't provision" in docs/design.md): an allocation whose first
    resource is of type Storage gets a share when it becomes Active, and an approved change request
    on one is applied only if it changes the quota attribute. Keep this in step with the plugin: if
    it says no when the plugin does act, the customer hears "done" before the storage exists.
    """
    resource = allocation.resources.select_related("resource_type").first()
    if resource is None or resource.resource_type.name != "Storage":
        return False
    return change is None or _quota_change(change) is not None


def _quota_change(change):
    """The change request's AllocationAttributeChangeRequest for the quota, or None."""
    return (
        change.allocationattributechangerequest_set.select_related("allocation_attribute")
        .filter(allocation_attribute__allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME)
        .first()
    )


def _public_attributes(allocation) -> list[dict]:
    """The allocation's attributes whose type isn't private, by name: what the customer sees in
    ColdFront. Tickets list only these, since the PI and Approvers are on them."""
    return [
        {"name": human_name(a.allocation_attribute_type.name), "value": a.value}
        for a in allocation.allocationattribute_set.filter(allocation_attribute_type__is_private=False)
        .select_related("allocation_attribute_type")
        .order_by("allocation_attribute_type__name")
    ]


def _public_changes(change):
    """The change request's attribute changes to types that aren't private (see _public_attributes)."""
    return change.allocationattributechangerequest_set.filter(
        allocation_attribute__allocation_attribute_type__is_private=False
    ).select_related("allocation_attribute__allocation_attribute_type")


def resource_label(allocation, resource) -> str:
    """What to call the allocation in a ticket: its resource's name, or its pk without one."""
    return resource.name if resource else f"Allocation {allocation.pk}"


@dataclass(frozen=True)
class AllocationUpdate:
    short_description: str
    comments: str  # customer-facing message
    close_notes: str
    business_service: str
    u_action: str  # U_ACTION_ACTIVATE for something new, U_ACTION_MOVE_ADD_CHANGE for a change


@dataclass(frozen=True)
class RequestDescription:
    short_description: str
    description: str  # admin-facing summary of what was asked for
    business_service: str
    u_action: str  # as for AllocationUpdate
    # customer-visible journal entry posted when the ticket opens; ServiceNow emails it to the caller
    comments: str | None = None


def describe_new_allocation(allocation, requestor) -> RequestDescription:
    """The incident content for a newly requested Allocation, whatever its resource."""
    resource = allocation.get_parent_resource
    resource_type = resource.resource_type.name if resource else None
    quota = allocation.get_attribute(QUOTA_ATTRIBUTE_NAME)

    summary = "New Allocation Request"
    if resource_type == "Storage" and quota is not None:
        summary += f" | {quota} {QUOTA_UNITS}"
    summary += f" | {allocation.project.title}"

    context = {
        "allocation": allocation,
        "project": allocation.project,
        "pi": allocation.project.pi,
        "requestor": requestor,
        "resource": resource,
        "attributes": _public_attributes(allocation),
        "url": build_link(allocation.get_absolute_url()),
    }
    return RequestDescription(
        short_description=clean_short_description(f"{resource_label(allocation, resource)}: {summary}"),
        description=render_to_string("ticketing/allocation_request.txt", context),
        business_service=_business_service(resource),
        u_action=U_ACTION_ACTIVATE,
    )


def describe_activation(allocation) -> AllocationUpdate:
    """The customer message for a newly activated Allocation: Storage's, or the generic one.

    Raises UnsupportedUpdate for an allocation with no resource."""
    resource = _resource(allocation)
    handler = _storage_activation if storage_will_provision(allocation) else _generic_activation
    return handler(allocation, resource)


def describe_request(change, requestor) -> RequestDescription:
    """The incident content for a newly submitted AllocationChangeRequest.

    Covers every change request: once core's ticket emails are gated off, this is the only ticket
    admins get for one. Values are as they stand at request time, so an attribute's
    current value is the one being changed from.
    """
    allocation = change.allocation
    resource = allocation.get_parent_resource
    resource_type = resource.resource_type.name if resource else None
    attribute_changes = [
        {
            "name": human_name(acr.allocation_attribute.allocation_attribute_type.name),
            "current": acr.allocation_attribute.value,
            "requested": acr.new_value,
        }
        for acr in _public_changes(change)
    ]
    path = allocation.get_attribute(PATH_ATTRIBUTE_NAME) or ""
    # The short description names the requested quota whether or not its type is private.
    quota_change = _quota_change(change)

    if resource_type == "Storage" and quota_change:
        summary = f"Quota Change Request | {quota_change.new_value} {QUOTA_UNITS} | {path}"
    else:
        summary = f"Allocation Change Request | {allocation.project.title}"

    context = {
        "change": change,
        "allocation": allocation,
        "project": allocation.project,
        "pi": allocation.project.pi,
        "requestor": requestor,
        "resource": resource,
        "path": path,
        "attribute_changes": attribute_changes,
        "url": build_link(change.get_absolute_url()),
    }
    return RequestDescription(
        short_description=clean_short_description(f"{resource_label(allocation, resource)}: {summary}"),
        description=render_to_string("ticketing/allocation_change_request.txt", context),
        business_service=_business_service(resource),
        u_action=U_ACTION_MOVE_ADD_CHANGE,
    )


def describe_change(change) -> AllocationUpdate:
    """The customer message for an approved AllocationChangeRequest: Storage's for a quota change,
    else the generic one listing what changed.

    Raises UnsupportedUpdate for an allocation with no resource."""
    resource = _resource(change.allocation)
    handler = _storage if storage_will_provision(change.allocation, change) else _generic_change
    return handler(change, resource)


def _resource(allocation):
    resource = allocation.get_parent_resource
    if resource is None:
        raise UnsupportedUpdate(f"Allocation {allocation.pk} has no resource")
    return resource


def _business_service(resource) -> str:
    """The ServiceNow business service for tickets about `resource` (which may be None)."""
    resource_type = resource.resource_type.name if resource else None
    return BUSINESS_SERVICE_BY_RESOURCE_TYPE.get(resource_type, DEFAULT_BUSINESS_SERVICE)


def _render(folder, resource, context) -> str:
    """The first of `<folder>/<resource name>.txt`, `<folder>/<resource type>.txt` and
    `<folder>/generic.txt` (slugified) that exists."""
    names = [slugify(resource.name), slugify(resource.resource_type.name), "generic"]
    return render_to_string([f"ticketing/{folder}/{name}.txt" for name in names], context)


def _previous_value(attribute, new_value):
    """The value `attribute` held before it became `new_value`, from its history (newest first)."""
    for record in attribute.history.all():
        if record.value != new_value:
            return record.value
    return None


def _storage(change, resource) -> AllocationUpdate:
    quota_change = _quota_change(change)
    new_quota = f"{quota_change.new_value} {QUOTA_UNITS}"
    old_value = _previous_value(quota_change.allocation_attribute, quota_change.new_value)
    old_quota = f"{old_value} {QUOTA_UNITS}" if old_value is not None else None
    path = change.allocation.get_attribute(PATH_ATTRIBUTE_NAME) or ""

    context = {
        "allocation": change.allocation,
        "change": change,
        "resource": resource,
        "old_quota": old_quota,
        "new_quota": new_quota,
        "path": path,
        # The RStore wording (rstore_storage_update.txt) calls the path a share name.
        "share_name": path,
    }
    return AllocationUpdate(
        short_description=f"{resource.name}: Quota Change | {new_quota} | {path}",
        comments=_render("allocation_update", resource, context),
        close_notes="Quota updated.",
        business_service=_business_service(resource),
        u_action=U_ACTION_MOVE_ADD_CHANGE,
    )


def _storage_activation(allocation, resource) -> AllocationUpdate:
    quota = allocation.get_attribute(QUOTA_ATTRIBUTE_NAME)
    quota = f"{quota} {QUOTA_UNITS}" if quota is not None else None
    path = allocation.get_attribute(PATH_ATTRIBUTE_NAME) or ""
    context = {
        "allocation": allocation,
        "project": allocation.project,
        "resource": resource,
        "quota": quota,
        "path": path,
        "share_name": path,
    }
    parts = [f"{resource.name}: New Allocation", quota, path or allocation.project.title]
    return AllocationUpdate(
        short_description=clean_short_description(" | ".join(p for p in parts if p)),
        comments=_render("allocation_new", resource, context),
        close_notes="Allocation activated.",
        business_service=_business_service(resource),
        u_action=U_ACTION_ACTIVATE,
    )


def _generic_activation(allocation, resource) -> AllocationUpdate:
    context = {
        "allocation": allocation,
        "project": allocation.project,
        "resource": resource,
        "attributes": _public_attributes(allocation),
    }
    return AllocationUpdate(
        short_description=clean_short_description(f"{resource.name}: New Allocation | {allocation.project.title}"),
        comments=_render("allocation_new", resource, context),
        close_notes="Allocation activated.",
        business_service=_business_service(resource),
        u_action=U_ACTION_ACTIVATE,
    )


def _generic_change(change, resource) -> AllocationUpdate:
    allocation = change.allocation
    attribute_changes = [
        {
            "name": human_name(acr.allocation_attribute.allocation_attribute_type.name),
            "old": _previous_value(acr.allocation_attribute, acr.new_value),
            "new": acr.new_value,
        }
        for acr in _public_changes(change)
    ]
    context = {
        "allocation": allocation,
        "project": allocation.project,
        "change": change,
        "resource": resource,
        "attribute_changes": attribute_changes,
    }
    return AllocationUpdate(
        short_description=clean_short_description(f"{resource.name}: Allocation Change | {allocation.project.title}"),
        comments=_render("allocation_change", resource, context),
        close_notes="Allocation change applied.",
        business_service=_business_service(resource),
        u_action=U_ACTION_MOVE_ADD_CHANGE,
    )
