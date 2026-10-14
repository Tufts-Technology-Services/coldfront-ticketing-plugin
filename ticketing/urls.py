from django.urls import path

from ticketing.views import LinkTicketView, TicketingToggleView

urlpatterns = [
    path("toggle/", TicketingToggleView.as_view(), name="ticketing-toggle"),
    path("link/<str:subject_type>/<int:pk>/", LinkTicketView.as_view(), name="ticketing-link"),
]
