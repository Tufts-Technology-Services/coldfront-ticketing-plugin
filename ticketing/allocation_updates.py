"""What an allocation-update ticket says, by what kind of allocation was updated.

Dispatches on the allocation's resource type (only "Storage" today). Within a type, the customer
message is the first template that exists of:

    ticketing/allocation_update/<resource name>.txt
    ticketing/allocation_update/<resource type>.txt

(names slugified), so a resource that needs its own wording -- e.g. HPC vs RStore storage -- gets a
template named after it, and every other resource of that type falls back to the type's template.
"""

from dataclasses import dataclass

from django.template.loader import render_to_string
from django.utils.text import slugify

# The one supported update today. Matches the storage plugin's QUOTA_ATTRIBUTE_NAME default.
QUOTA_ATTRIBUTE_NAME = "Storage Quota (TB)"
QUOTA_UNITS = "TB"
PATH_ATTRIBUTE_NAME = "Storage Path"


class UnsupportedUpdate(Exception):
    """The change request isn't one this plugin writes a ticket for."""


@dataclass(frozen=True)
class AllocationUpdate:
    short_description: str
    comments: str  # customer-facing message
    close_notes: str
    business_service: str


def describe_change(change) -> AllocationUpdate:
    """The ticket content for an approved AllocationChangeRequest; raises UnsupportedUpdate."""
    resource = change.allocation.get_parent_resource
    if resource is None:
        raise UnsupportedUpdate(f"Allocation {change.allocation.pk} has no resource")
    resource_type = resource.resource_type.name
    describe = _BY_RESOURCE_TYPE.get(resource_type)
    if describe is None:
        raise UnsupportedUpdate(f"No update ticket for resource type {resource_type!r}")
    return describe(change, resource)


def _render(resource, context) -> str:
    names = [
        f"ticketing/allocation_update/{slugify(resource.name)}.txt",
        f"ticketing/allocation_update/{slugify(resource.resource_type.name)}.txt",
    ]
    return render_to_string(names, context)


def _previous_value(attribute, new_value):
    """The value `attribute` held before it became `new_value`, from its history (newest first)."""
    for record in attribute.history.all():
        if record.value != new_value:
            return record.value
    return None


def _storage(change, resource) -> AllocationUpdate:
    quota_change = (
        change.allocationattributechangerequest_set.select_related("allocation_attribute")
        .filter(allocation_attribute__allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME)
        .first()
    )
    if quota_change is None:
        raise UnsupportedUpdate(f"Change request {change.pk} has no {QUOTA_ATTRIBUTE_NAME} change")

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
        comments=_render(resource, context),
        close_notes="Quota updated.",
        business_service="Research Data Storage",
    )


_BY_RESOURCE_TYPE = {
    "Storage": _storage,
}
