import logging

import pytest
from django.contrib.sessions.backends.db import SessionStore
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


def htmx_post(client):
    return client.post(toggle_url(), headers={"HX-Request": "true"})


def test_toggle_flips_the_admins_switch_and_returns_the_control(admin, client):
    client.force_login(admin)

    response = htmx_post(client)
    assert response.status_code == 200
    assert client.session[toggle.SESSION_KEY] is True
    assert "Tickets off" in response.content.decode()

    response = htmx_post(client)
    assert client.session[toggle.SESSION_KEY] is False
    assert "Tickets on" in response.content.decode()


def test_ticketing_admins_can_use_the_toggle(ticketing_admin, client):
    client.force_login(ticketing_admin)

    assert htmx_post(client).status_code == 200
    assert client.session[toggle.SESSION_KEY] is True


def test_toggle_is_ticketing_admins_only(requestor, client):
    client.force_login(requestor)

    assert htmx_post(client).status_code == 403
    assert toggle.SESSION_KEY not in client.session


def test_toggle_sends_anonymous_users_to_log_in(client):
    response = htmx_post(client)

    assert response.status_code == 302
    assert "login" in response.headers["Location"]


def test_toggle_accepts_only_post(admin, client):
    client.force_login(admin)

    assert client.get(toggle_url()).status_code == 405


def test_toggle_without_htmx_redirects_back(admin, client):
    client.force_login(admin)

    response = client.post(toggle_url(), headers={"Referer": "http://testserver/allocation/"})
    assert response.status_code == 302 and response.headers["Location"] == "http://testserver/allocation/"

    response = client.post(toggle_url(), headers={"Referer": "https://elsewhere.example/"})
    assert response.headers["Location"] == reverse("home")


def test_history_middleware_is_active_on_real_requests(admin, client):
    # The runtime check the whole feature rests on, through ColdFront's actual middleware stack.
    client.force_login(admin)

    assert htmx_post(client).context["ticketing_toggle"]["available"] is True


def test_toggle_refuses_when_the_history_middleware_is_missing(admin, client, settings, caplog):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if m != HISTORY_MIDDLEWARE]
    client.force_login(admin)

    with caplog.at_level(logging.ERROR, logger="ticketing.views"):
        response = htmx_post(client)

    assert toggle.SESSION_KEY not in client.session
    assert "Ticket switch unavailable" in response.content.decode()
    assert "HistoryRequestMiddleware is not active" in caplog.text


@pytest.mark.parametrize("who", ["admin", "ticketing_admin"])
def test_context_processor_gives_ticketing_admins_their_switch(client, request, who):
    client.force_login(request.getfixturevalue(who))
    htmx_post(client)

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
