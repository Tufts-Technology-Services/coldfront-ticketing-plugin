import pytest
from coldfront.core.allocation.signals import allocation_new
from coldfront.core.allocation.views import AllocationCreateView
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.test import override_settings
from django.urls import reverse

from ticketing.permissions import is_ticketing_admin
from ticketing.tests.test_allocation_updates import make_storage_allocation
from ticketing.tests.test_filed_tickets import file

pytestmark = pytest.mark.django_db


def test_superusers_and_permission_holders_are_ticketing_admins(admin, ticketing_admin):
    assert is_ticketing_admin(admin)
    assert is_ticketing_admin(ticketing_admin)


def test_is_staff_alone_isnt_enough(is_staff_user, requestor):
    assert not is_ticketing_admin(is_staff_user)
    assert not is_ticketing_admin(requestor)


def test_the_permission_can_come_from_a_group(django_user_model, is_staff_user):
    group = Group.objects.create(name="Research Technology")
    group.permissions.add(Permission.objects.get(codename="manage_ticketing"))
    is_staff_user.groups.add(group)

    assert is_ticketing_admin(django_user_model.objects.get(pk=is_staff_user.pk))


def test_a_deactivated_superuser_still_counts(admin):
    admin.is_active = False  # has_perm is False for inactive users

    assert is_ticketing_admin(admin)


def test_nobody_is_not_a_ticketing_admin():
    assert not is_ticketing_admin(None)
    assert not is_ticketing_admin(AnonymousUser())


def test_an_is_staff_users_request_opens_a_ticket(
    pi, is_staff_user, in_request, queued, django_capture_on_commit_callbacks
):
    allocation, _ = make_storage_allocation(pi)
    in_request(is_staff_user)

    with django_capture_on_commit_callbacks(execute=True):
        allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk)

    assert queued == [
        ("ticketing.tasks.create_allocation_ticket", (allocation.pk,), {"requestor_pk": is_staff_user.pk})
    ]


def test_is_staff_users_dont_get_the_switch(is_staff_user, client):
    client.force_login(is_staff_user)

    assert client.post(reverse("ticketing-toggle")).status_code == 403


@override_settings(SERVICENOW_URL="https://servicenow.invalid")
def test_ticketing_admins_can_view_filed_tickets_in_django_admin(ticketing_admin, client):
    ticketing_admin.is_staff = True  # Django admin only lets in is_staff users
    ticketing_admin.save()
    file("coldfront:allocation:1")
    client.force_login(ticketing_admin)

    assert client.get(reverse("admin:ticketing_filedticket_changelist")).status_code == 200
    assert client.get(reverse("admin:ticketing_filedticket_add")).status_code == 403


def test_is_staff_alone_cant_view_filed_tickets_in_django_admin(is_staff_user, client):
    client.force_login(is_staff_user)

    assert client.get(reverse("admin:ticketing_filedticket_changelist")).status_code == 403
