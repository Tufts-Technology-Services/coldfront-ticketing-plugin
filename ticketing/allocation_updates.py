"""What allocation tickets say, by what kind of allocation it is.

Two lifecycles, each with two moments:

    new allocation   describe_new_allocation()  opened on request   admin-facing, every allocation
                     describe_activation()      resolved on active  customer-facing, supported types
    change request   describe_request()         opened on request   admin-facing, every change
                     describe_change()          resolved on approve customer-facing, supported changes

The customer-facing side dispatches on the allocation's resource type (only "Storage" today), and
the message is the first template that exists of

    ticketing/<folder>/<resource name>.txt
    ticketing/<folder>/<resource type>.txt

(names slugified; folder allocation_new/ or allocation_update/). So a resource that needs its own
wording -- e.g. HPC vs RStore storage -- gets a template named after it, and every other resource of
that type falls back to the type's template.
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
    """The allocation or change isn't one this plugin writes a customer message for."""


@dataclass(frozen=True)
class AllocationUpdate:
    short_description: str
    comments: str  # customer-facing message
    close_notes: str
    business_service: str


@dataclass(frozen=True)
class RequestDescription:
    short_description: str
    description: str  # admin-facing summary of what was asked for
    business_service: str | None  # None: leave the Ticket default


def describe_new_allocation(allocation, requestor) -> RequestDescription:
    """The incident content for a newly requested Allocation -- every one, supported or not."""
    resource = allocation.get_parent_resource
    resource_type = resource.resource_type.name if resource else None
    attributes = [
        {"name": a.allocation_attribute_type.name, "value": a.value}
        for a in allocation.allocationattribute_set.select_related("allocation_attribute_type")
    ]
    quota = allocation.get_attribute(QUOTA_ATTRIBUTE_NAME)

    label = resource.name if resource else f"Allocation {allocation.pk}"
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
        "attributes": attributes,
        "url": build_link(reverse("allocation-detail", kwargs={"pk": allocation.pk})),
    }
    return RequestDescription(
        short_description=clean_short_description(f"{label}: {summary}"),
        description=render_to_string("ticketing/allocation_request.txt", context),
        business_service=BUSINESS_SERVICE_BY_RESOURCE_TYPE.get(resource_type),
    )


def describe_activation(allocation) -> AllocationUpdate:
    """The customer message for a newly activated Allocation; raises UnsupportedUpdate."""
    resource = _supported_resource(allocation, _ACTIVATION_BY_RESOURCE_TYPE)
    return _ACTIVATION_BY_RESOURCE_TYPE[resource.resource_type.name](allocation, resource)


def describe_request(change, requestor) -> RequestDescription:
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
    return RequestDescription(
        short_description=clean_short_description(f"{label}: {summary}"),
        description=render_to_string("ticketing/allocation_change_request.txt", context),
        business_service=BUSINESS_SERVICE_BY_RESOURCE_TYPE.get(resource_type),
    )


def describe_change(change) -> AllocationUpdate:
    """The customer message for an approved AllocationChangeRequest; raises UnsupportedUpdate."""
    resource = _supported_resource(change.allocation, _CHANGE_BY_RESOURCE_TYPE)
    return _CHANGE_BY_RESOURCE_TYPE[resource.resource_type.name](change, resource)


def _supported_resource(allocation, handlers):
    resource = allocation.get_parent_resource
    if resource is None:
        raise UnsupportedUpdate(f"Allocation {allocation.pk} has no resource")
    if resource.resource_type.name not in handlers:
        raise UnsupportedUpdate(f"No customer message for resource type {resource.resource_type.name!r}")
    return resource


def _render(folder, resource, context) -> str:
    names = [
        f"ticketing/{folder}/{slugify(resource.name)}.txt",
        f"ticketing/{folder}/{slugify(resource.resource_type.name)}.txt",
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
        comments=_render("allocation_update", resource, context),
        close_notes="Quota updated.",
        business_service=BUSINESS_SERVICE_BY_RESOURCE_TYPE["Storage"],
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
        business_service=BUSINESS_SERVICE_BY_RESOURCE_TYPE["Storage"],
    )


_CHANGE_BY_RESOURCE_TYPE = {
    "Storage": _storage,
}
_ACTIVATION_BY_RESOURCE_TYPE = {
    "Storage": _storage_activation,
}
