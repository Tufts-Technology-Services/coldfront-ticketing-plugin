import pytest
from django.contrib.auth.models import Permission
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from simple_history.models import HistoricalRecords

from ticketing import dispatch, toggle


@pytest.fixture
def requestor(django_user_model):
    """The user who submits a request. Their username is their UTLN."""
    return django_user_model.objects.create_user(username="jdoe01", email="jdoe01@example.edu", password="pw")


@pytest.fixture
def pi(django_user_model):
    return django_user_model.objects.create_user(username="asmith02", email="asmith02@example.edu", password="pw")


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_superuser(username="admin01", email="admin01@example.edu", password="pw")


@pytest.fixture
def is_staff_user(django_user_model):
    """A user with Django's is_staff and nothing more: not a ticketing admin."""
    return django_user_model.objects.create_user(
        username="staff01", email="staff01@example.edu", password="pw", is_staff=True
    )


@pytest.fixture
def ticketing_admin(django_user_model):
    """A user granted ticketing.manage_ticketing who is neither a superuser nor is_staff."""
    user = django_user_model.objects.create_user(username="tadmin01", email="tadmin01@example.edu", password="pw")
    user.user_permissions.add(Permission.objects.get(content_type__app_label="ticketing", codename="manage_ticketing"))
    return django_user_model.objects.get(pk=user.pk)  # refetched: has_perm caches on the instance


@pytest.fixture
def queued(monkeypatch):
    """Records what enqueue_ticket hands to django-q."""
    calls = []
    monkeypatch.setattr(dispatch, "async_task", lambda func, *args, **kwargs: calls.append((func, args, kwargs)))
    return calls


@pytest.fixture
def in_request():
    """Stand in for HistoryRequestMiddleware: stash a request for `user`, as a view would see it."""

    def stash(user, off=False):
        request = RequestFactory().post("/")
        request.user = user
        request.session = SessionStore()
        request.session[toggle.SESSION_KEY] = off
        HistoricalRecords.context.request = request
        return request

    yield stash
    try:
        del HistoricalRecords.context.request
    except AttributeError:
        pass
