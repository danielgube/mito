r"""Regenera las capturas del README desde Textual, sin operaciones de usuario.

Ejecutar desde la raíz: core\tools\python\python.exe -B -I docs/tools/capture_readme.py
Usa el inventario local y el profile default; oculta referencias del host por privacidad.
"""
import asyncio
import os
from pathlib import Path
import sys
from unittest.mock import patch
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'core/app'))
os.environ.pop('NO_COLOR', None)
os.environ['FORCE_COLOR'] = '1'
os.environ['COLORTERM'] = 'truecolor'

from mito.engine import Engine
from mito.i18n import Translator
from mito.ui import YitoApp
from textual.widgets import Button, ContentSwitcher, Input


class CaptureApp(YitoApp):
    def refresh_terminal_default(self):
        pass

    def on_checkbox_changed(self, event):
        # Capturar nunca activa componentes ni escribe filtros.
        pass

    def on_unmount(self):
        pass


async def main():
    engine = Engine(ROOT)
    # glob puede ocultar carpetas inaccesibles; no publicar un inventario vacío falso.
    missing = set(engine.profile('default')['apps']) - set(engine.installed())
    if missing:
        raise RuntimeError('No se pueden leer los componentes activos: ' + ', '.join(sorted(missing)))
    app = CaptureApp(engine, profile='default')
    app.translator = Translator('español')
    app.disabled_buckets = set()
    output = ROOT / 'docs/images'
    with ExitStack() as stack:
        stack.enter_context(patch('mito.host_apps.inventory', return_value={}))
        stack.enter_context(patch('mito.terminal_identity.apply_safely'))
        for method in ('check_network', 'check_component_updates', 'check_mito_update', 'refresh_terminal_default'):
            stack.enter_context(patch.object(YitoApp, method))
        # run_test es aquí el renderizador headless, no una suite de pruebas.
        async with app.run_test(size=(170, 58)) as pilot:
            app.query_one('#network-status', Button).label = 'Red · sin comprobar'
            await pilot.pause()
            (output / 'mito-inicio.svg').write_text(app.export_screenshot(title='MITO · Inicio'), encoding='utf-8')
            app.query_one('#pages', ContentSwitcher).current = 'catalog-page'
            app.query_one('#search', Input).value = 'java 21 lts'
            app.load_catalog()
            app.query_one('#search', Input).focus()
            await pilot.pause()
            (output / 'mito-catalogo.svg').write_text(app.export_screenshot(title='MITO · Catálogo'), encoding='utf-8')
    print('Capturas guardadas en docs/images')


if __name__ == '__main__':
    asyncio.run(main())

