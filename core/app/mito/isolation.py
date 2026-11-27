"""Profile isolation determined only by declarative manifest fields."""
from .i18n import tr
from pathlib import Path
import os
import shutil
import tempfile
from .catalog import effective
from .storage import identifier, inside, rename_released, YitoError
from .persistence import definitions, prepare


def isolated(lock):
    manifest = effective(lock['manifest'])
    return bool(manifest.get('persist') and manifest.get('shortcuts'))


def persist_root(root, lock, profile):
    prefix = root / 'home/profiles' / identifier(profile) / 'persist' if isolated(lock) else root / 'home/persist'
    return inside(prefix, lock['key'])


def payload_path(root, lock, profile):
    base = inside(root, lock['path'])
    if not isolated(lock):
        return base
    return inside(root / 'core/profile-apps' / identifier(profile), base.relative_to(root / 'core/apps').as_posix())


def prepare_payload(root, lock, profile):
    """Caller holds the mutation lock. Publish a complete independent copy."""
    base = inside(root,lock['path'])
    target = payload_path(root,lock,profile)
    manifest = effective(lock['manifest'])
    if isolated(lock) and not target.exists():
        excluded = {base.joinpath(*Path(relative.replace('\\','/')).parts) for relative,_ in definitions(manifest)}
        def ignore(folder,names):
            folder = Path(folder)
            return [name for name in names if folder/name in excluded or (folder == base and name == '.mito-persist.json')]
        # Never follow any inherited junction when copying the installation template.
        for folder,dirs,files in os.walk(base,followlinks=False):
            for name in dirs+files:
                path=Path(folder)/name
                if path in excluded:
                    if name in dirs:dirs.remove(name)
                    continue
                if path.is_junction() or path.is_symlink():
                    raise YitoError(tr('Enlace no admitido en la copia del componente: {p1}', p1=(path)))
        with tempfile.TemporaryDirectory(dir=root/'core/staging') as tmp:
            copy = Path(tmp)/'payload'
            shutil.copytree(base,copy,ignore=ignore)
            for relative,_ in definitions(manifest):
                source = base / relative
                defaults = inside(copy/'.mito-defaults',relative)
                if not defaults.exists() and source.exists() and not source.is_junction() and not source.is_symlink():
                    defaults.parent.mkdir(parents=True,exist_ok=True)
                    if source.is_dir():shutil.copytree(source,defaults)
                    else:shutil.copy2(source,defaults)
            target.parent.mkdir(parents=True,exist_ok=True)
            rename_released(copy, target)
    prepare(root,target,lock['key'],manifest,persist_root(root,lock,profile))
    return target
