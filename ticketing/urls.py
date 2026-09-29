from django.urls import path

from ticketing.views import TicketingToggleView

urlpatterns = [
    path("toggle/", TicketingToggleView.as_view(), name="ticketing-toggle"),
]
