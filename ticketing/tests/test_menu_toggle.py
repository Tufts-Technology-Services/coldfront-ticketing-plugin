"""
The ticketing switch in the user menu, and the navbar's sign that it is off.

The switch itself is a rarely used, per-session control, so it lives in the user menu
(user_menu_toggle.html). The navbar shows something only when ticketing is off, or when the switch
can't work (navbar_off_indicator.html). Both post a plain form to the toggle view, which redirects
back to the page, so the page reloads showing the new state in both places.
"""

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

pytestmark = pytest.mark.django_db

ON = {"off": False, "available": True}
OFF = {"off": True, "available": True}
UNAVAILABLE = {"off": False, "available": False}


def render_with(template_name, state, user):
    request = RequestFactory().get("/allocation/")
    request.user = user
    # the context processors run too, and the ticketing one reads the session
    request.session = SessionStore()
    return render_to_string(f"ticketing/{template_name}", {"ticketing_toggle": state}, request=request)


def posts_to_the_toggle(html):
    return (
        f'action="{reverse("ticketing-toggle")}"' in html
        and 'method="post"' in html
        and 'name="csrfmiddlewaretoken"' in html
    )


class TestNavbarOffIndicator:
    def test_shows_nothing_while_ticketing_is_on(self, admin):
        assert render_with("navbar_off_indicator.html", ON, admin).strip() == ""

    def test_shows_that_ticketing_is_off_as_a_button_that_turns_it_back_on(self, admin):
        html = render_with("navbar_off_indicator.html", OFF, admin)

        assert 'id="navbar-ticketing-off"' in html
        assert posts_to_the_toggle(html)
        assert "off" in html

    def test_says_when_the_switch_cannot_work(self, admin):
        html = render_with("navbar_off_indicator.html", UNAVAILABLE, admin)

        assert "Ticket switch unavailable" in html
        assert "<form" not in html

    def test_is_a_plain_form_so_the_page_reloads(self, admin):
        """Not htmx: the menu item has to change too, and the toggle view redirects a plain post back."""
        assert "hx-post" not in render_with("navbar_off_indicator.html", OFF, admin)


class TestUserMenuToggle:
    def test_while_on_offers_to_turn_it_off(self, admin):
        html = render_with("user_menu_toggle.html", ON, admin)

        assert 'id="navbar-user-ticketing"' in html
        assert posts_to_the_toggle(html)
        assert "turn off" in html

    def test_while_off_offers_to_turn_it_on(self, admin):
        html = render_with("user_menu_toggle.html", OFF, admin)

        assert posts_to_the_toggle(html)
        assert "turn on" in html

    def test_says_when_the_switch_cannot_work(self, admin):
        html = render_with("user_menu_toggle.html", UNAVAILABLE, admin)

        assert "Ticket switch unavailable" in html
        assert "<form" not in html

    def test_is_a_plain_form_so_the_page_reloads(self, admin):
        assert "hx-post" not in render_with("user_menu_toggle.html", ON, admin)


def test_turning_it_back_on_from_the_navbar_returns_to_the_page(admin, client):
    """The indicator's form is a plain post: the toggle view sends it back where it came from."""
    client.force_login(admin)
    client.post(reverse("ticketing-toggle"))  # off

    response = client.post(reverse("ticketing-toggle"), headers={"Referer": "http://testserver/allocation/"})

    assert response.status_code == 302
    assert response.headers["Location"] == "http://testserver/allocation/"
