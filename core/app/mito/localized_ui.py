"""Translate per-instance bindings without changing Textual's class definitions."""
from dataclasses import replace
from textual.screen import ModalScreen as TextualModalScreen
from .i18n import tr


def translate_bindings(node, translate=tr):
    for key, bindings in node._bindings.key_to_bindings.items():
        node._bindings.key_to_bindings[key] = [
            replace(binding, description=translate(binding.description)) for binding in bindings
        ]


class ModalScreen[Result](TextualModalScreen[Result]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        translate_bindings(self)
