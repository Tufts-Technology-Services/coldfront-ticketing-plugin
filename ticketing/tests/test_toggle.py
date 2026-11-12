import logging

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import RequestFactory
from django.urls import reverse

from ticketing import dispatch, toggle
from ticketing.context_processors import ticketing_toggle

pytestmark = pytest.mark.django_db

HISTORY_MIDDLEWARE = "simple_history.middleware.HistoryRequestMiddleware"


def enqueue(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        dispatch.enqueue_ticket("ticketing.tasks.create_hpc_account_ticket", "jdoe01", home_path="/cluster/home/jdoe01")


def test_enqueue_ticket_queues_outside_a_request(queued, django_capture_on_commit_callbacks):
    enqueue(django_capture_on_commit_callbacks)

    assert queued == [("ticketing.tasks.create_hpc_account_ticket", ("jdoe01",), {"home_path": "/cluster/home/jdoe01"})]


def test_enqueue_ticket_queues_for_an_admin_with_ticketing_on(
    admin, in_request, queued, django_capture_on_commit_callbacks
):
    in_request(admin, off=False)
    enqueue(django_capture_on_commit_callbacks)

    assert len(queued) == 1


def test_enqueue_ticket_queues_nothing_for_an_admin_with_ticketing_off(
    admin, in_request, queued, django_capture_on_commit_callbacks
):
    in_request(admin, off=True)
    enqueue(django_capture_on_commit_callbacks)

    assert queued == []


def test_ticketing_admins_who_arent_superusers_can_switch_ticketing_off(
    ticketing_admin, in_request, queued, django_capture_on_commit_callbacks
):
    in_request(ticketing_admin, off=True)
    enqueue(django_capture_on_commit_callbacks)

    assert queued == []


def test_only_ticketing_admins_can_switch_ticketing_off(
    requestor, in_request, queued, django_capture_on_commit_callbacks
):
    # e.g. a former ticketing admin whose session still carries the flag
    in_request(requestor, off=True)
    enqueue(django_capture_on_commit_callbacks)

    assert len(queued) == 1


def test_enqueue_ticket_logs_rather_than_raising_when_queueing_fails(
    monkeypatch, caplog, django_capture_on_commit_callbacks
):
    def broken(*args, **kwargs):
        raise ConnectionError("Redis is down")

    monkeypatch.setattr(dispatch, "async_task", broken)
    with caplog.at_level(logging.ERROR, logger="ticketing.dispatch"):
        enqueue(django_capture_on_commit_callbacks)

    assert "Could not queue ticketing.tasks.create_hpc_account_ticket" in caplog.text


def test_enqueue_ticket_waits_for_the_transaction_to_commit(queued, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        dispatch.enqueue_ticket("ticketing.tasks.create_hpc_account_ticket", "jdoe01")

    assert queued == [] and len(callbacks) == 1


def toggle_url():
    return reverse("ticketing-toggle")


def post_to_toggle(client, **headers):
    return client.post(toggle_url(), headers=headers)


def test_toggle_flips_the_admins_switch_and_redirects_back(admin, client):
    client.force_login(admin)
    page = "http://testserver/allocation/"

    response = post_to_toggle(client, Referer=page)
    assert response.status_code == 302 and response.headers["Location"] == page
    assert client.session[toggle.SESSION_KEY] is True

    post_to_toggle(client, Referer=page)
    assert client.session[toggle.SESSION_KEY] is False


def test_toggle_redirects_htmx_posts_too(admin, client):
    # The menu-bar control htmx swapped in is gone; the menu item and the navbar indicator both
    # need the page to reload.
    client.force_login(admin)

    response = post_to_toggle(client, **{"HX-Request": "true", "Referer": "http://testserver/allocation/"})

    assert response.status_code == 302 and response.headers["Location"] == "http://testserver/allocation/"
    assert client.session[toggle.SESSION_KEY] is True


def test_the_old_navbar_toggle_template_is_gone():
    with pytest.raises(TemplateDoesNotExist):
        get_template("ticketing/navbar_toggle.html")


def test_ticketing_admins_can_use_the_toggle(ticketing_admin, client):
    client.force_login(ticketing_admin)

    assert post_to_toggle(client).status_code == 302
    assert client.session[toggle.SESSION_KEY] is True


def test_toggle_is_ticketing_admins_only(requestor, client):
    client.force_login(requestor)

    assert post_to_toggle(client).status_code == 403
    assert toggle.SESSION_KEY not in client.session


def test_toggle_sends_anonymous_users_to_log_in(client):
    response = post_to_toggle(client)

    assert response.status_code == 302
    assert "login" in response.headers["Location"]


def test_toggle_accepts_only_post(admin, client):
    client.force_login(admin)

    assert client.get(toggle_url()).status_code == 405


def test_toggle_redirects_home_when_the_referer_is_off_site(admin, client):
    client.force_login(admin)

    response = post_to_toggle(client, Referer="https://elsewhere.example/")
    assert response.headers["Location"] == reverse("home")

    response = post_to_toggle(client)
    assert response.headers["Location"] == reverse("home")


def test_history_middleware_is_active_on_real_requests(admin, client):
    # The runtime check the whole feature rests on, through ColdFront's actual middleware stack.
    client.force_login(admin)

    assert client.get(reverse("home")).context["ticketing_toggle"]["available"] is True


def test_toggle_refuses_when_the_history_middleware_is_missing(admin, client, settings, caplog):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if m != HISTORY_MIDDLEWARE]
    client.force_login(admin)

    with caplog.at_level(logging.ERROR, logger="ticketing.views"):
        response = post_to_toggle(client)

    assert response.status_code == 302
    assert toggle.SESSION_KEY not in client.session
    assert "HistoryRequestMiddleware is not active" in caplog.text


@pytest.mark.parametrize("who", ["admin", "ticketing_admin"])
def test_context_processor_gives_ticketing_admins_their_switch(client, request, who):
    client.force_login(request.getfixturevalue(who))
    post_to_toggle(client)

    response = client.get(reverse("home"))

    assert response.context["ticketing_toggle"] == {"off": True, "available": True}


def test_context_processor_gives_other_users_nothing(requestor, client):
    client.force_login(requestor)

    response = client.get(reverse("home"))

    assert "ticketing_toggle" not in response.context


def test_context_processor_reports_the_switch_unavailable_without_the_middleware(admin):
    # A request the middleware never stashed.
    request = RequestFactory().get("/")
    request.user = admin
    request.session = SessionStore()

    assert ticketing_toggle(request) == {"ticketing_toggle": {"off": False, "available": False}}
