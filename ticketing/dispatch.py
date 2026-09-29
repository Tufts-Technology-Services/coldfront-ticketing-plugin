"""The one way ticketing tasks get queued.

Receivers and triggers call `enqueue_ticket` rather than `async_task`, so that:

- an admin who has switched ticketing off (see `ticketing.toggle`) files nothing, resolves included;
- the handoff waits for the surrounding transaction to commit, so the task re-queries committed state;
- a queueing failure (Redis down) is logged instead of raising into the user's request.
"""

import logging

from django.db import transaction
from django_q.tasks import async_task

from ticketing import toggle

logger = logging.getLogger(__name__)


def enqueue_ticket(func, *args, **kwargs):
    """Queue the ticketing task `func` (a dotted path) unless the acting admin has ticketing off.

    The switch is read now, while the request is certainly still being handled, not when the
    transaction commits. Outside a request (management commands, django-q workers) there is no
    switch to read, so the task is queued.
    """
    request = toggle.current_request()
    if request is not None and toggle.is_off(request):
        logger.info("Ticketing is off for %s; not queuing %s%r", request.user.username, func, args)
        return
    transaction.on_commit(lambda: _queue(func, args, kwargs))


def _queue(func, args, kwargs):
    try:
        async_task(func, *args, **kwargs)
    except Exception:
        logger.exception("Could not queue %s%r", func, args)
