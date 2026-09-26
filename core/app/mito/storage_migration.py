"""Explicit one-time conversion from version directories to stable component paths."""
import shutil
import uuid
from .catalog import fingerprint
from .storage import YitoError, inside, mutation, read_json, write_json
from .components import check_unused, refresh_launchers


def migrate(engine, log=print):
    with mutation(engine.root):
        plans, aliases, records = [], {}, {}
        for marker in sorted((engine.root/'core/apps').glob('*/*/*/.mito.json')):
            record = read_json(marker)['record']
            key = record['key']
            destination = inside(engine.root, 'core/apps/' + key)
            if marker.parent.parent != destination or key in records or (destination/'.mito.json').exists():
                raise YitoError(f'Migración ambigua para {key}: conserva una única instalación antes de migrar; no se ha cambiado nada.')
            if fingerprint(record['manifest']) != record['manifest_sha256']:
                raise YitoError(f'Manifiesto incoherente: {key}')
            aliases[key + '@' + record['manifest_sha256'][:16]] = key
            records[key] = dict(record, path='core/apps/' + key)
            plans.append((marker.parent, destination, records[key]))
            for folder in (engine.root/'core/profile-apps').glob('*'):
                old = inside(folder, marker.parent.relative_to(engine.root/'core/apps').as_posix())
                if old.exists():
                    plans.append((old, inside(folder, key), records[key]))
        if not plans:
            log('El almacenamiento ya utiliza rutas estables.')
            return
        for source, destination, record in plans:
            if source.is_junction() or source.is_symlink() or destination.is_junction() or destination.is_symlink():
                raise YitoError('Ruta enlazada no válida para migrar')
            if list(destination.iterdir()) != [source]:
                raise YitoError(f'Contenido adicional en {destination}; migración cancelada sin borrar archivos.')
            check_unused(source)
        backups = {name: engine.profile(name) for name in engine.profiles()}
        for value in backups.values():
            for key in value['apps']:
                if '@' in key and key not in aliases:
                    raise YitoError(f'Referencia antigua sin instalación: {key}')
        stage = inside(engine.root, 'core/staging/migrate-' + uuid.uuid4().hex)
        stage.mkdir()
        moved, written, links = [], [], {}
        metadata = {}
        try:
            for index, (source, destination, record) in enumerate(plans):
                stored = stage/str(index)
                metadata[source] = (source/'.mito.json').read_bytes()
                destination.rename(stored)
                moved.append((source, destination, stored))
                (stored/source.name).rename(destination)
                write_json(destination/'.mito.json', {'key':record['key'], 'manifest_sha256':record['manifest_sha256'], 'record':record})
            for name, value in backups.items():
                engine.save_profile(name, dict(value, apps=list(dict.fromkeys(aliases.get(key, key) for key in value['apps']))))
                written.append(name)
            from .shortcuts import migrate_application_links
            for old, key in aliases.items():
                migrate_application_links(engine, old, key, records[key], links, log)
            refresh_launchers(engine, backups)
        except Exception:
            for path, content in links.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
            for source, destination, stored in reversed(moved):
                if destination.exists():
                    destination.rename(stored/source.name)
                stored.rename(destination)
                (source/'.mito.json').write_bytes(metadata[source])
            for name in written:
                engine.save_profile(name, backups[name])
            shutil.rmtree(stage)
            raise
        shutil.rmtree(stage)
        log(f'Migrados {len(records)} componentes y {len(plans)-len(records)} copias por profile. Datos conservados.')
