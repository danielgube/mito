from .i18n import tr
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
from .storage import YitoError, inside, mutation, read_json, write_json
from .transfer import download, extract_zip
from .diagnostics import step, write


def ensure_tools(root):
    lock = read_json(root / 'core/bootstrap.lock.json')
    with mutation(root):
        for tool in lock.get('tools', []):
            write(root, 'info', 'bootstrap.tools', 'Preparing tool', component=tool['id'])
            destination = root / 'core/tools' / tool['id']
            marker = destination / '.mito-tool.json'
            if marker.exists() and read_json(marker).get('version') == tool['version'] and all(inside(destination, f).is_file() for f in tool['required']):
                continue
            if destination.exists():
                raise YitoError(tr('Herramienta incompleta: {p1}. Conservada para revisar antes de sustituirla.', p1=(destination)))
            print(f'Preparando {tool["id"]} {tool["version"]}…', flush=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
                stage = Path(tmp)
                payload = stage / 'payload'
                payload.mkdir()
                for artifact in tool['artifacts']:
                    archive = stage / artifact['fileName']
                    with step(root, 'bootstrap.tools', 'download-and-hash', tool['id']):
                        download(artifact['url'], archive, artifact['sha256'], root=root)
                    install = artifact['install']
                    if install['type'] == 'file':
                        shutil.copy2(archive, inside(payload, install.get('target', archive.name)))
                    elif install['type'] == 'zip':
                        expanded = stage / 'expanded'
                        with step(root, 'bootstrap.tools', 'extract-zip', tool['id']):
                            extract_zip(archive, expanded)
                        shutil.copytree(inside(expanded, install.get('source', '.')), payload, dirs_exist_ok=True)
                    elif install['type'] == 'sevenZip':
                        # Bootstrap archive and extractor are both pinned core artifacts.
                        with step(root, 'bootstrap.tools', 'extract-7zip', tool['id']):
                            subprocess.run([str(inside(payload, install['executable'])), 'x', str(archive), f'-o{payload}', '-y'], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        raise YitoError(tr('Formato de herramienta core desconocido'))
                if not all(inside(payload, f).is_file() for f in tool['required']):
                    raise YitoError(tr('Herramienta incompleta: {p1}', p1=(tool['id'])))
                if tool['id'] == 'windows-terminal':
                    (payload / '.portable').touch()
                write_json(payload / '.mito-tool.json', {'version': tool['version']})
                payload.rename(destination)
        # A fresh release is branded before mito.cmd opens its first window.
        from .terminal import brand_window_icons
        brand_window_icons(root)
        write_json(root / 'core/state/bootstrap-tools.json', {'lock_sha256': hashlib.sha256((root/'core/bootstrap.lock.json').read_bytes()).hexdigest()})
