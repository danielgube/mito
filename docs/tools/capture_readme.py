r"""Regenera las capturas del README desde Textual, sin operaciones de usuario.

Ejecutar desde la raíz: core\tools\python\python.exe -B -I docs/tools/capture_readme.py
Usa el inventario local y el profile default; oculta referencias del host por privacidad.
"""
import asyncio
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
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


def interface_only(svg):
    """Conserva el área de Textual y elimina el marco de ventana añadido por Rich."""
    namespace = 'http://www.w3.org/2000/svg'
    ET.register_namespace('', namespace)
    tag = lambda name: f'{{{namespace}}}{name}'
    root = ET.fromstring(svg)
    terminal = next(child for child in root
                    if child.tag == tag('g') and child.get('clip-path', '').endswith('-clip-terminal)'))
    clip_id = terminal.get('clip-path')[5:-1]
    clip = next(node for node in root.iter(tag('clipPath')) if node.get('id') == clip_id)
    bounds = clip.find(tag('rect'))
    root.set('viewBox', f"0 0 {bounds.get('width')} {bounds.get('height')}")
    terminal.attrib.pop('transform', None)
    for child in list(root):
        if child is not terminal and child.tag not in (tag('style'), tag('defs')):
            root.remove(child)
    return ET.tostring(root, encoding='unicode') + '\n'


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
            (output / 'mito-inicio.svg').write_text(interface_only(app.export_screenshot()), encoding='utf-8')
            app.query_one('#pages', ContentSwitcher).current = 'catalog-page'
            app.query_one('#search', Input).value = 'java 21 lts'
            app.load_catalog()
            app.query_one('#search', Input).focus()
            await pilot.pause()
            (output / 'mito-catalogo.svg').write_text(interface_only(app.export_screenshot()), encoding='utf-8')
    print('Capturas guardadas en docs/images')


if __name__ == '__main__':
    asyncio.run(main())

