"""Who the plugin treats as a ticketing admin: superusers, and users granted manage_ticketing.

Ticketing admins get the ticketing switch, the ticket card and its link form, and the FiledTicket
admin pages. Their allocation and change requests open no ticket, and adding or removing them from a
project files no notice. Grant the permission to a user or a group in Django admin.
"""

MANAGE_TICKETING = "ticketing.manage_ticketing"


def is_ticketing_admin(user) -> bool:
    # is_superuser is checked first because has_perm is False for inactive users, superusers
    # included, and a deactivated admin's old requests should still count as an admin's.
    return user is not None and (user.is_superuser or user.has_perm(MANAGE_TICKETING))
