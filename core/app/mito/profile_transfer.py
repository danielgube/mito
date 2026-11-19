"""Share catalog selections only; never serialize profiles or installation records.

El archivo compartido contiene los componentes activos (bucket/app) y, para los
retenidos, la versión elegida. Nada más: ni variables, ni datos, ni integraciones
con el host (aplicaciones del host, enlaces «En host», «Abrir con»), que dependen
de la máquina y de la instancia de origen.
"""
from .i18n import tr
import json
from pathlib import Path
from .storage import YitoError, identifier, mutation, read_json


def export_profile(engine, profile, destination):
    records = engine.selected(engine.profile(profile))
    data = {'apps': list(dict.fromkeys(record['key'] for record in records.values()))}
    pinned = {record['key']: str(record['pinned']['version']) for record in records.values()
              if isinstance(record.get('pinned'), dict) and record['pinned'].get('version')}
    if pinned:
        data['pinned'] = pinned
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite an existing file (including an instance profile).
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return destination


def component_key(value):
    if not isinstance(value, str) or len(value.split('/')) != 2:
        raise YitoError(tr('Cada componente debe tener formato bucket/app'))
    bucket, name = value.split('/')
    return identifier(bucket) + '/' + identifier(name)


def read_export(source):
    """{'apps': [bucket/app], 'pinned': {bucket/app: versión}} validado."""
    from .versions import validate_version
    source = Path(source).expanduser()
    if source.stat().st_size > 1024 * 1024:
        raise YitoError(tr('El archivo de profile exportado es demasiado grande'))
    data = read_json(source)
    if (not isinstance(data, dict) or 'apps' not in data or not set(data) <= {'apps', 'pinned'}
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


def import_profile(engine, source, name, confirm_plan, confirm_unverified=None, log=print):
    name = identifier(name)
    if name in engine.profiles():
        raise YitoError(tr('Ya existe ese profile; elige otro nombre'))
    shared = read_export(source)
    if confirm_plan is None:
        raise YitoError(tr('La importación requiere revisar y confirmar los componentes'))
    # Las versiones retenidas en el origen se reproducen y quedan retenidas aquí.
    manifests, pins = {}, {}
    if shared['pinned']:
        from .versions import resolve
        for key, version in shared['pinned'].items():
            log(tr('Consultando versiones de {p1}…', p1=key))
            manifests[key], pins[key] = resolve(engine.root, engine.catalog, key, version, log=log)
    # One dependency plan and one no-hash confirmation for the entire import.
    records = engine.install(shared['apps'], log=log, confirm_plan=confirm_plan,
                             confirm_unverified=confirm_unverified, manifests=manifests, pins=pins)
    with mutation(engine.root):
        if name in engine.profiles():
            raise YitoError(tr('Ya existe ese profile; las instalaciones quedan disponibles'))
        (engine.root/'workspaces'/name).mkdir(parents=True, exist_ok=True)
        engine.save_profile(name, {'workspace': name, 'apps': list(records), 'env': {}})
        from .terminal import prepare
        prepare(engine)
    log(tr('Profile {p1} importado con {p3} componentes activos', p1=(name), p3=(len(records))))
    return name
