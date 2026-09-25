import importlib

from coldfront.core.utils.common import import_from_settings
from django.apps import AppConfig

TICKETING_ENABLE_SIGNALS = import_from_settings("TICKETING_ENABLE_SIGNALS", False)


class TicketingConfig(AppConfig):
    name = "ticketing"

    def ready(self):
        if TICKETING_ENABLE_SIGNALS:
            importlib.import_module("ticketing.signals")
