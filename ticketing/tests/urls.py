"""URLConf for the tests.

coldfront's `common/base.html` and navbars reverse a lot of core URL names, so the plugin's routes
are mounted alongside coldfront's real URLConf rather than on their own.
"""

from coldfront.config.urls import urlpatterns as coldfront_urlpatterns
from django.urls import include, path

urlpatterns = [
    *coldfront_urlpatterns,
    path("ticketing/", include("ticketing.urls")),
]
