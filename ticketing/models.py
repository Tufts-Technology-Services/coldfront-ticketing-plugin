"""A local record of the ServiceNow tickets this plugin files, for linking to them from ColdFront.

ServiceNow stays the source of truth: a FiledTicket says which incident was filed for what, and when
this plugin resolved it, but not the incident's current state, which changes there.
"""

from urllib.parse import urlencode

from coldfront.core.allocation.models import Allocation, AllocationChangeRequest
from coldfront.core.project.models import Project
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from model_utils.models import TimeStampedModel


class FiledTicketQuerySet(models.QuerySet):
    def about(self, subject):
        """Tickets about an Allocation (including its change requests), a Project (including its
        allocations') or a User (their HPC account)."""
        if isinstance(subject, Allocation):
            return self.filter(allocation=subject)
        if isinstance(subject, Project):
            return self.filter(Q(project=subject) | Q(allocation__project=subject))
        if isinstance(subject, get_user_model()):
            return self.filter(user=subject)
        raise TypeError(f"No tickets are filed about a {type(subject).__name__}")


class FiledTicket(TimeStampedModel):
    """A ServiceNow ticket this plugin filed, and what it's about.

    The subject links are all nullable and SET_NULL, so deleting a user, allocation or project keeps
    the record; `correlation_id` still says what the ticket was for. A change request's ticket links
    the allocation too, so it is listed with the allocation's own.
    """

    class Kind(models.TextChoices):
        ALLOCATION = "allocation", "Allocation request"
        ALLOCATION_CHANGE = "allocation-change", "Allocation change request"
        HPC_ACCOUNT = "hpc-account", "HPC account"
        PROJECT_USERS = "project-users", "Project users changed"

    class Source(models.TextChoices):
        FILED = "filed", "Filed by ColdFront"
        LINKED = "linked", "Linked from ServiceNow"

    correlation_id = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    # LINKED: filed in ServiceNow directly, then adopted by an admin (ticketing.linking).
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.FILED)
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    table = models.CharField(max_length=40, default="incident")
    number = models.CharField(max_length=40)
    sys_id = models.CharField(max_length=32)
    # When this plugin resolved it, or filed it already resolved. Null while open, and when someone
    # else resolved it in ServiceNow.
    resolved_at = models.DateTimeField(null=True, blank=True)

    allocation = models.ForeignKey(
        Allocation, null=True, blank=True, on_delete=models.SET_NULL, related_name="filed_tickets"
    )
    allocation_change = models.ForeignKey(
        AllocationChangeRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="filed_tickets"
    )
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="filed_tickets")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="filed_tickets"
    )

    objects = FiledTicketQuerySet.as_manager()

    class Meta:
        ordering = ["-created"]
        # The plugin's one permission, checked by ticketing.permissions.is_ticketing_admin. It lives
        # here because a permission needs a model; it isn't about FiledTicket rows.
        permissions = [("manage_ticketing", "Can use the ServiceNow ticketing controls")]

    def __str__(self):
        return f"{self.number} ({self.correlation_id})"

    @property
    def url(self):
        """The ticket in ServiceNow, on whichever instance SERVICENOW_URL names now."""
        base = settings.SERVICENOW_URL.rstrip("/")
        return f"{base}/nav_to.do?{urlencode({'uri': f'{self.table}.do?sys_id={self.sys_id}'})}"
