"""Share catalog selections and chosen configuration files; never serialize profiles.

El archivo compartido contiene los componentes activos (bucket/app), para los
retenidos la versión elegida y, si la tabla `files` del profile tiene entradas,
esos archivos de configuración tal cual (ZIP). Nada más: ni variables, ni
integraciones con el host (aplicaciones del host, enlaces «En host», «Abrir con»),
que dependen de la máquina y de la instancia de origen. Sin archivos, el formato
sigue siendo el JSON anterior.
"""
from .i18n import tr
import json
from pathlib import Path
import zipfile
from .profile_files import component_key
from .storage import YitoError, identifier, mutation, read_json


def export_profile(engine, profile, destination, log=print):
    from . import profile_files
    data = engine.profile(profile)
    records = engine.selected(data)
    shared = {'apps': list(dict.fromkeys(record['key'] for record in records.values()))}
    pinned = {record['key']: str(record['pinned']['version']) for record in records.values()
              if isinstance(record.get('pinned'), dict) and record['pinned'].get('version')}
    if pinned:
        shared['pinned'] = pinned
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if profile_files.entries(data.get('files', [])):
        present, members = profile_files.pack(engine, profile, data, records, log)
        return profile_files.write_archive(destination, {'format': 1, **shared, 'files': present}, members)
    # Never overwrite an existing file (including an instance profile).
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(shared, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return destination


def validate_shared(data):
    from .versions import validate_version
    if (not isinstance(data, dict) or 'apps' not in data
            or not isinstance(data['apps'], list) or not isinstance(data.get('pinned', {}), dict)):
        raise YitoError(tr('El archivo compartido debe contener únicamente una lista apps'))
    apps = []
    for app in data['apps']:
        key = component_key(app)
        if key not in apps:
            apps.append(key)
    pinned = {}
    for app, version in data.get('pinned', {}).items():
        key = component_key(app)
        if key not in apps or not isinstance(version, str):
            raise YitoError(tr('Cada componente debe tener formato bucket/app'))
        pinned[key] = validate_version(version)
    return {'apps': apps, 'pinned': pinned}


def read_export(source):
    """JSON {'apps', 'pinned'} o ZIP con además 'files' y 'contents', validado."""
    source = Path(source).expanduser()
    if zipfile.is_zipfile(source):
        from .profile_files import read_archive, invalid
        manifest, contents = read_archive(source)
        shared = validate_shared(manifest)
        for entry in manifest['files']:
            if entry['root'] == 'persist' and entry['app'] not in shared['apps']:
                raise invalid(entry['app'])
        return dict(shared, files=manifest['files'], contents=contents)
    if source.stat().st_size > 1024 * 1024:
        raise YitoError(tr('El archivo de profile exportado es demasiado grande'))
    data = read_json(source)
    if not isinstance(data, dict) or not set(data) <= {'apps', 'pinned'}:
        raise YitoError(tr('El archivo compartido debe contener únicamente una lista apps'))
    return validate_shared(data)


def import_profile(engine, source, name, confirm_plan, confirm_unverified=None, log=print):
    """confirm_plan(records) sin archivos; con archivos, confirm_plan(records, items)
    devuelve False para cancelar, True o los destinos (str) que se sobrescriben."""
    name = identifier(name)
    if name in engine.profiles():
        raise YitoError(tr('Ya existe ese profile; elige otro nombre'))
    shared = read_export(source)
    if confirm_plan is None:
        raise YitoError(tr('La importación requiere revisar y confirmar los componentes'))
    reviewed = {}

    def review(records):
        if not shared.get('contents'):
            return confirm_plan(records)
        from .profile_files import plan
        items = plan(engine, name, records, shared['contents'])
        result = confirm_plan(records, items)
        if result is None or result is False:
            return False
        reviewed['items'] = items
        reviewed['overwrite'] = set() if result is True else {str(target) for target in result}
        return True
    # Las versiones retenidas en el origen se reproducen y quedan retenidas aquí.
    manifests, pins = {}, {}
    if shared['pinned']:
        from .versions import resolve
        for key, version in shared['pinned'].items():
            log(tr('Consultando versiones de {p1}…', p1=key))
            manifests[key], pins[key] = resolve(engine.root, engine.catalog, key, version, log=log)
    # One dependency plan and one no-hash confirmation for the entire import.
    records = engine.install(shared['apps'], log=log, confirm_plan=review,
                             confirm_unverified=confirm_unverified, manifests=manifests, pins=pins)
    with mutation(engine.root):
        if name in engine.profiles():
            raise YitoError(tr('Ya existe ese profile; las instalaciones quedan disponibles'))
        if reviewed:
            from .profile_files import write
            write(engine, name, records, reviewed['items'], reviewed['overwrite'], log)
        (engine.root/'workspaces'/name).mkdir(parents=True, exist_ok=True)
        data = {'workspace': name, 'apps': list(records), 'env': {}}
        if shared.get('files'):
            data['files'] = shared['files']
        engine.save_profile(name, data)
        from .terminal import prepare
        prepare(engine)
    log(tr('Profile {p1} importado con {p3} componentes activos', p1=(name), p3=(len(records))))
    return name
