"""Share catalog selections only; never serialize profiles or installation records."""
from .i18n import tr
import json
from pathlib import Path
from .storage import YitoError, identifier, mutation, read_json


def export_profile(engine, profile, destination):
    records = engine.selected(engine.profile(profile))
    data = {'apps': list(dict.fromkeys(record['key'] for record in records.values()))}
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite an existing file (including an instance profile).
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return destination


def read_export(source):
    source = Path(source).expanduser()
    if source.stat().st_size > 1024 * 1024:
        raise YitoError(tr('El archivo de profile exportado es demasiado grande'))
    data = read_json(source)
    if not isinstance(data, dict) or set(data) != {'apps'} or not isinstance(data['apps'], list):
        raise YitoError(tr('El archivo compartido debe contener únicamente una lista apps'))
    apps = []
    for app in data['apps']:
        if not isinstance(app, str) or len(app.split('/')) != 2:
            raise YitoError(tr('Cada componente debe tener formato bucket/app'))
        bucket, name = app.split('/')
        key = identifier(bucket) + '/' + identifier(name)
        if key not in apps:
            apps.append(key)
    return apps


def import_profile(engine, source, name, confirm_plan, confirm_unverified=None, log=print):
    name = identifier(name)
    if name in engine.profiles():
        raise YitoError(tr('Ya existe ese profile; elige otro nombre'))
    apps = read_export(source)
    if confirm_plan is None:
        raise YitoError(tr('La importación requiere revisar y confirmar los componentes'))
    # One dependency plan and one no-hash confirmation for the entire import.
    records = engine.install(apps, log=log, confirm_plan=confirm_plan,
                             confirm_unverified=confirm_unverified)
    with mutation(engine.root):
        if name in engine.profiles():
            raise YitoError(tr('Ya existe ese profile; las instalaciones quedan disponibles'))
        (engine.root/'workspaces'/name).mkdir(parents=True, exist_ok=True)
        engine.save_profile(name, {'workspace': name, 'apps': list(records), 'env': {}})
        from .terminal import prepare
        prepare(engine)
    log(tr('Profile {p1} importado con {p3} componentes activos', p1=(name), p3=(len(records))))
    return name
