"""Seeing which templates a ticket's text came from, and what they were given.

Tests check the code's part of a message, not its wording: which template (and partials) it
picked, and the data it handed them. The wording belongs to the templates and can change without
touching a test; docs/servicenow-ticket-examples shows the text itself, and
test_ticket_examples.py keeps those pages current.

Django sends template_rendered for every template and include while the test environment is set
up, as pytest-django does for the whole run.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field

from django.test.signals import template_rendered


@dataclass
class Rendered:
    """The templates rendered inside capture_templates(), in order, each with its flattened context."""

    templates: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.templates]

    @property
    def main(self) -> str:
        """The template the code picked: the first rendered, since Django renders a template before its includes."""
        return self.templates[0][0]

    @property
    def main_context(self) -> dict:
        return self.templates[0][1]

    def context(self, name: str) -> dict:
        """The context `name` was last rendered with. KeyError if it wasn't rendered."""
        for rendered_name, context in reversed(self.templates):
            if rendered_name == name:
                return context
        raise KeyError(f"{name} was not rendered; rendered: {self.names}")


@contextmanager
def capture_templates():
    """Record every template rendered in the block: `with capture_templates() as rendered: ...`."""
    rendered = Rendered()

    def record(sender, template, context, **kwargs):
        rendered.templates.append((template.name, context.flatten()))

    template_rendered.connect(record)
    try:
        yield rendered
    finally:
        template_rendered.disconnect(record)
