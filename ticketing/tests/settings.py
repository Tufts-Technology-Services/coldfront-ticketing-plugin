"""Django settings for the plugin's test suite.

This layers on top of coldfront's own settings rather than hand-rolling a minimal config. The plugin
can't be meaningfully isolated from coldfront: it receives coldfront's signals, and
`coldfront.core.utils.mail` -- which this plugin sends through -- reads a dozen `EMAIL_*` settings at
import time. Reusing the real settings keeps those working.
"""

from coldfront.config.settings import *  # noqa: F403

# The plugin is not in coldfront's own INSTALLED_APPS; a real deployment adds it
# the way README.md describes.
INSTALLED_APPS = [*INSTALLED_APPS, "ticketing"]  # noqa: F405

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Settings the plugin reads that coldfront itself does not define.
TICKETING_ENABLE_SIGNALS = True
TICKETING_ASSIGNED_TO = "svc_test"

# Keep tests fast and hermetic.
DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
Q_CLUSTER = {**globals().get("Q_CLUSTER", {}), "sync": True}
