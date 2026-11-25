"""Windows MAX_PATH guard for the portable root.

Without LongPathsEnabled, Python and many tools cannot handle paths over 260
characters. Third-party trees (node_modules, Electron resources) are installed
under core/apps and staged under core/staging, so the instance must live near
the drive root. Same contract as Get-YitoRootPathProblem in bootstrap.ps1."""
import os
from pathlib import Path

from .i18n import tr

MAX_ROOT = 32


def long_paths_enabled():
    if os.name != 'nt':
        return True
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\FileSystem') as key:
            return winreg.QueryValueEx(key, 'LongPathsEnabled')[0] == 1
    except OSError:
        return False


def problem(root, enabled=long_paths_enabled):
    """Message explaining why this root is too long, or None when it is acceptable."""
    full = os.path.abspath(Path(root)).rstrip('\\/')
    if len(full) <= MAX_ROOT or enabled():
        return None
    return tr('La carpeta de MITO tiene una ruta demasiado larga ({p1} caracteres; máximo {p2}): {p3}\n'
              'Windows limita las rutas a 260 caracteres y algunas aplicaciones no se podrían instalar.\n'
              'Mueve la carpeta más cerca de la raíz del disco, por ejemplo C:\\mito, y vuelve a ejecutar mito.cmd.',
              p1=len(full), p2=MAX_ROOT, p3=full)
