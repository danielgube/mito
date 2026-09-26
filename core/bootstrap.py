"""Install the exact, hashed wheel closure without pip or a global Python."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'app'))
from mito.storage import write_json
from mito.transfer import download, extract_zip
from mito import diagnostics
from mito.error_tracking import capture, flush, resume

PHASE = 'startup'
COMPONENT = None


def main():
    global PHASE, COMPONENT
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    core = Path(__file__).resolve().parent
    diagnostics.write(core.parent, 'info', 'bootstrap.python', 'Bootstrap Python started', runtime=diagnostics.environment())
    resume(core.parent)
    PHASE = 'read-bootstrap-lock'
    lock = json.loads((core / 'bootstrap.lock.json').read_text())
    cache = core / 'wheels'
    cache.mkdir(exist_ok=True)
    for item in lock['wheels']:
        COMPONENT = item['filename']
        PHASE = 'download-wheel'
        diagnostics.write(core.parent, 'info', 'bootstrap.python', COMPONENT, phase=PHASE)
        wheel = cache / item['filename']
        if not wheel.exists() or hashlib.sha256(wheel.read_bytes()).hexdigest() != item['sha256']:
            print(f'Preparando {item["filename"]}', flush=True)
            download(item['url'], wheel, item['sha256'], root=core.parent)
        PHASE = 'extract-wheel'
        extract_zip(wheel, args.target)
    COMPONENT = None
    PHASE = 'configure-python-path'
    pth = next(args.target.glob('python*._pth'))
    stdlib = next(args.target.glob('python3*.zip')).name
    pth.write_text(f'{stdlib}\n.\n../../app\nimport site\n', encoding='ascii')
    PHASE = 'validate-runtime-imports'
    subprocess.run([str(args.target / 'python.exe'), '-B', '-I', '-c', 'import textual, PIL; from mcp.server.lowlevel import Server; from importlib.metadata import version; print("Textual", version("textual"), "Pillow", PIL.__version__, "MCP", version("mcp"))'], check=True)
    PHASE = 'prepare-tools'
    from mito.bootstrap_tools import ensure_tools
    ensure_tools(core.parent)
    PHASE = 'publish-ready-marker'
    write_json(args.target / 'mito-ready.json', {'schema': 1, 'lock_sha256': hashlib.sha256((core / 'bootstrap.lock.json').read_bytes()).hexdigest()})
    diagnostics.write(core.parent, 'info', 'bootstrap.python', 'Bootstrap Python completed')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        capture(Path(__file__).resolve().parent.parent, exc, handled=False,
                operation='bootstrap.python', phase=PHASE, component=COMPONENT)
        raise
    finally:
        flush()
