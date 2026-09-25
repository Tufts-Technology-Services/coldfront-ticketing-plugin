"""What allocation change-request tickets say, by what kind of allocation is being changed.

Two moments: describe_request() for the incident opened when a change is requested (admin-facing,
covers every change request), and describe_change() for the message posted when an approved change
has been made (customer-facing, supported changes only).

describe_change() dispatches on the allocation's resource type (only "Storage" today). Within a
type, the customer message is the first template that exists of:

    ticketing/allocation_update/<resource name>.txt
    ticketing/allocation_update/<resource type>.txt

(names slugified), so a resource that needs its own wording -- e.g. HPC vs RStore storage -- gets a
template named after it, and every other resource of that type falls back to the type's template.
"""

from dataclasses import dataclass

from coldfront.core.utils.mail import build_link
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.text import slugify

from ticketing.tickets import clean_short_description

# The one supported update today. Matches the storage plugin's QUOTA_ATTRIBUTE_NAME default.
QUOTA_ATTRIBUTE_NAME = "Storage Quota (TB)"
QUOTA_UNITS = "TB"
PATH_ATTRIBUTE_NAME = "Storage Path"


# ServiceNow business service per resource type; types not listed keep the Ticket default (HPC).
BUSINESS_SERVICE_BY_RESOURCE_TYPE = {
    "Storage": "Research Data Storage",
}


class UnsupportedUpdate(Exception):
    """The change request isn't one this plugin writes a ticket for."""


@dataclass(frozen=True)
class AllocationUpdate:
    short_description: str
    comments: str  # customer-facing message
    close_notes: str
    business_service: str


@dataclass(frozen=True)
class ChangeRequestDescription:
    short_description: str
    description: str  # admin-facing summary of what was asked for
    business_service: str | None  # None: leave the Ticket default


def describe_request(change, requestor) -> ChangeRequestDescription:
    """The incident content for a newly submitted AllocationChangeRequest.

    Covers every change request, supported or not: once core's ticket emails are gated off, this is
    the only ticket admins get for one. Values are as they stand at request time, so an attribute's
    current value is the one being changed from.
    """
    allocation = change.allocation
    resource = allocation.get_parent_resource
    resource_type = resource.resource_type.name if resource else None
    attribute_changes = [
        {
            "name": acr.allocation_attribute.allocation_attribute_type.name,
            "current": acr.allocation_attribute.value,
            "requested": acr.new_value,
        }
        for acr in change.allocationattributechangerequest_set.select_related(
            "allocation_attribute__allocation_attribute_type"
        )
    ]
    path = allocation.get_attribute(PATH_ATTRIBUTE_NAME) or ""
    quota = next((c for c in attribute_changes if c["name"] == QUOTA_ATTRIBUTE_NAME), None)

    label = resource.name if resource else f"Allocation {allocation.pk}"
    if resource_type == "Storage" and quota:
        summary = f"Quota Change Request | {quota['requested']} {QUOTA_UNITS} | {path}"
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
        "url": build_link(reverse("allocation-change-detail", kwargs={"pk": change.pk})),
    }
    return ChangeRequestDescription(
        short_description=clean_short_description(f"{label}: {summary}"),
        description=render_to_string("ticketing/allocation_change_request.txt", context),
        business_service=BUSINESS_SERVICE_BY_RESOURCE_TYPE.get(resource_type),
    )


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
        business_service=BUSINESS_SERVICE_BY_RESOURCE_TYPE["Storage"],
    )


_BY_RESOURCE_TYPE = {
    "Storage": _storage,
}
