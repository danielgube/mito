"""Private, per-install snapshots for explicit $bucketsdir references in hooks."""
from pathlib import Path
import re
import shutil
import tempfile

from .storage import YitoError, identifier, inside, read_json
from .transfer import download, extract_zip, MAX_EXPANDED


def required_buckets(key, manifest):
    text = '\n'.join(str(manifest.get(field, '')) for field in ('pre_install', 'installer'))
    references = list(re.finditer(r'\$(?:bucketsdir\b|\{bucketsdir\})', text, re.I))
    result = set()
    for reference in references:
        # JSON/repr may double slashes. Only the bucket segment is interpreted.
        tail = text[reference.end():].lstrip('\\/')
        match = re.match(r'(\$\{bucket\}|\$bucket\b|[\w.-]+)[\\/]+scripts(?:[\\/]|\b)', tail, re.I)
        if not match:
            raise YitoError('Pasos no soportados: referencia dinámica a $bucketsdir')
        name = identifier(key.split('/')[0] if match[1].startswith('$') else match[1])
        result.add(name)
    return sorted(result)


def sources(catalog, key, manifest):
    result = {}
    configured = catalog.sources()
    for name in required_buckets(key, manifest):
        index = catalog.folder / f'{name}.json'
        source = configured.get(name) or (read_json(index).get('source') if index.exists() else None)
        if not source:
            raise YitoError(f'No hay origen registrado para los recursos del catálogo: {name}')
        if source.startswith(('http://', 'https://')) and not re.fullmatch(
                r'https?://github.com/([\w-]+)/([\w.-]+)', source.rstrip('/')):
            raise YitoError('La fuente debe ser un repositorio GitHub o una carpeta local')
        result[name] = source
    return result


def prepare(root, origins, destination, log):
    """Called only after the installation plan's unverified-download approval."""
    destination.mkdir(parents=True, exist_ok=True)
    for name, origin in origins.items():
        log(f'{name}: preparando recursos del catálogo')
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            scratch = Path(temporary)
            if origin.startswith(('http://', 'https://')):
                match = re.fullmatch(r'https?://github.com/([\w-]+)/([\w.-]+)', origin.rstrip('/'))
                if not match:
                    raise YitoError('La fuente debe ser un repositorio GitHub o una carpeta local')
                archive = scratch / 'repository.zip'
                download(f'https://api.github.com/repos/{match[1]}/{match[2]}/zipball', archive, root=root)
                expanded = scratch / 'repository'
                extract_zip(archive, expanded)
                entries = list(expanded.iterdir())
                if len(entries) != 1 or not entries[0].is_dir():
                    raise YitoError('El snapshot del catálogo debe tener una sola raíz')
                source = entries[0]
            else:
                source = Path(origin)
                # Accept a repository root or its bucket directory.
                if source.name.casefold() == 'bucket' and not (source/'scripts').is_dir():
                    source = source.parent
            scripts = source / 'scripts'
            if not scripts.is_dir() or scripts.is_symlink() or scripts.is_junction():
                raise YitoError(f'No hay recursos scripts válidos en el catálogo: {name}')
            files, total = [], 0
            for count, path in enumerate(scripts.rglob('*'), 1):
                if path.is_symlink() or path.is_junction():
                    raise YitoError('Los enlaces del catálogo no están permitidos')
                target = inside(destination / identifier(name) / 'scripts', path.relative_to(scripts).as_posix())
                if path.is_file():
                    files.append((path, target))
                    total += path.stat().st_size
                if count > 150000 or total > MAX_EXPANDED:
                    raise YitoError('Recursos del catálogo demasiado grandes')
            for path, target in files:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
