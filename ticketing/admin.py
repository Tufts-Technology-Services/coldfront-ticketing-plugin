from django.contrib import admin
from django.utils.html import format_html

from ticketing.models import FiledTicket
from ticketing.permissions import is_ticketing_admin


@admin.register(FiledTicket)
class FiledTicketAdmin(admin.ModelAdmin):
    """Read-only: the tasks write these. Deleting one only forgets the link; the ticket stays."""

    list_display = ("number", "kind", "correlation_id", "created", "resolved_at", "link")
    list_filter = ("kind",)
    search_fields = ("number", "correlation_id", "user__username", "project__title")
    date_hierarchy = "created"

    # Ticketing admins can view without a separate view permission. Django admin still only lets in
    # users with is_staff.
    def has_module_permission(self, request):
        return super().has_module_permission(request) or is_ticketing_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) or is_ticketing_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="ServiceNow")
    def link(self, obj):
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">Open</a>', obj.url)
