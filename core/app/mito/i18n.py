"""MITO messages only; catalog data and external output are never translated.

Spanish source templates are stable bundle keys. Interpolation happens after
lookup, so paths, component IDs and user values never become lookup keys.
The translator belongs to the running UI, including its Textual workers; CLI
and bootstrap callers retain the Spanish default without importing Textual.
"""
from functools import lru_cache
import ctypes
import json
from pathlib import Path
from string import Formatter
import sys


DEFAULT_LANGUAGE = 'english'
DEFAULT_SETTING = 'system'
LANGUAGES = (
    'español', 'english', 'български', 'hrvatski', 'čeština', 'dansk',
    'nederlands', 'eesti', 'suomi', 'français', 'deutsch', 'ελληνικά',
    'magyar', 'gaeilge', 'italiano', 'latviešu', 'lietuvių', 'malti',
    'polski', 'português', 'română', 'slovenčina', 'slovenščina', 'svenska',
)
LANGUAGE_CHOICES = (DEFAULT_SETTING, *LANGUAGES)
LOCALES = Path(__file__).with_name('locales')


def system_language():
    """Windows display language, including regional variants of EU languages.

    Use the user's UI language, not the regional date/number format or the
    language of this Python process. Unknown languages and API failures use English.
    """
    try:
        get_language = ctypes.WinDLL('kernel32', use_last_error=True).GetUserDefaultUILanguage
        get_language.argtypes = []
        get_language.restype = ctypes.c_ushort
        primary_language = get_language() & 0x3ff
        return {
            0x02: 'български', 0x05: 'čeština', 0x06: 'dansk',
            0x07: 'deutsch', 0x08: 'ελληνικά', 0x09: 'english',
            0x0a: 'español', 0x0b: 'suomi', 0x0c: 'français',
            0x0e: 'magyar', 0x10: 'italiano', 0x13: 'nederlands',
            0x15: 'polski', 0x16: 'português', 0x18: 'română',
            0x1a: 'hrvatski', 0x1b: 'slovenčina', 0x1d: 'svenska',
            0x24: 'slovenščina', 0x25: 'eesti', 0x26: 'latviešu',
            0x27: 'lietuvių', 0x3a: 'malti', 0x3c: 'gaeilge',
        }.get(primary_language, DEFAULT_LANGUAGE)
    except (AttributeError, OSError):
        return DEFAULT_LANGUAGE


def fields(template):
    return {name for _, name, _, _ in Formatter().parse(template) if name is not None}


@lru_cache(maxsize=len(LANGUAGES))
def bundle(language):
    if language not in LANGUAGES:
        language = DEFAULT_LANGUAGE
    try:
        data = json.loads((LOCALES / f'{language}.json').read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return {}
        valid = {}
        for key, value in data.items():
            if isinstance(value, str):
                try:
                    if fields(key) == fields(value):
                        valid[key] = value
                except ValueError:
                    pass
        return valid
    except (OSError, ValueError):
        return {}


class Translator:
    def __init__(self, language=DEFAULT_SETTING):
        if language == DEFAULT_SETTING:
            language = system_language()
        self.language = language if language in LANGUAGES else DEFAULT_LANGUAGE
        self.messages = bundle(self.language)

    @classmethod
    def from_root(cls, root):
        from .configuration import parse
        path = Path(root) / 'config.properties'
        values = parse(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        return cls(values.get('mito.language', DEFAULT_SETTING))

    def __call__(self, message, **values):
        template = self.messages.get(message, bundle(DEFAULT_LANGUAGE).get(message, message))
        return template.format(**values) if values else template


def tr(message, **values):
    # Textual thread workers have their own active_worker context, not active_app.
    context = sys.modules.get('textual._context')
    app = context.active_app.get(None) if context else None
    if app is None:
        worker_module = sys.modules.get('textual.worker')
        worker = worker_module.active_worker.get(None) if worker_module else None
        if worker is not None:
            app = worker.node.app
    translator = getattr(app, 'translator', None)
    if translator is not None:
        return translator(message, **values)
    return message.format(**values) if values else message
