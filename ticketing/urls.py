from django.urls import path

from ticketing.views import LinkTicketView, TicketingToggleView, UnlinkTicketView

urlpatterns = [
    path("toggle/", TicketingToggleView.as_view(), name="ticketing-toggle"),
    path("link/<str:subject_type>/<int:pk>/", LinkTicketView.as_view(), name="ticketing-link"),
    path("unlink/<int:pk>/", UnlinkTicketView.as_view(), name="ticketing-unlink"),
]
