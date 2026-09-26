"""Localize fixed labels in MITO's native Windows dialogs.

Only whole PowerShell string literals are substituted. Escape quotes in bundle
text so a translation remains data; paths and credentials keep their existing
separate input channels.
"""
from .i18n import tr


def localize_script(script, *labels, translate=tr):
    def literal(value):
        return "'" + value.replace("'", "''") + "'"
    for label in labels:
        script = script.replace(literal(label), literal(translate(label)))
    return script
