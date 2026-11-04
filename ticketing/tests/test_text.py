from ticketing.text import DEFAULT_SITE_NAME, render_ticket_text, site_name


def test_the_site_name_defaults_to_coldfront(settings):
    del settings.TICKETING_SITE_NAME

    assert site_name() == DEFAULT_SITE_NAME == "ColdFront"


def test_every_template_gets_the_site_name_from_the_setting(settings):
    settings.TICKETING_SITE_NAME = "Other Portal"

    text = render_ticket_text("ticketing/partials/allocation_link.txt", {"allocation_url": "/allocation/1/"})

    assert "Other Portal" in text and text.endswith("/allocation/1/")
