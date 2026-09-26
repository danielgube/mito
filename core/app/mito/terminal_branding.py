"""Create a MITO-branded copy of the private Windows Terminal executable."""
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import struct
import uuid

from .storage import YitoError, read_json, write_json


RT_ICON = 3
RT_GROUP_ICON = 14
LOAD_LIBRARY_AS_DATAFILE = 0x00000002
LOAD_LIBRARY_AS_IMAGE_RESOURCE = 0x00000020


def _resource(value):
    return ctypes.c_void_p(value) if isinstance(value, int) else ctypes.c_wchar_p(value)


def _ico_frames(path):
    data = path.read_bytes()
    if len(data) < 6:
        raise YitoError('El ICO de MITO está truncado')
    reserved, kind, count = struct.unpack_from('<HHH', data)
    if reserved != 0 or kind != 1 or count < 1 or len(data) < 6 + count * 16:
        raise YitoError('El ICO de MITO no es válido')
    frames = []
    for index in range(count):
        entry = struct.unpack_from('<BBBBHHII', data, 6 + index * 16)
        size, offset = entry[6], entry[7]
        if size < 1 or offset < 6 + count * 16 or offset + size > len(data):
            raise YitoError('El ICO de MITO contiene una imagen inválida')
        frames.append((entry[:6], data[offset:offset + size]))
    return frames


def _names_and_languages(path):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
    kernel.LoadLibraryExW.restype = ctypes.c_void_p
    kernel.FreeLibrary.argtypes = [ctypes.c_void_p]
    kernel.EnumResourceNamesW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
    kernel.EnumResourceLanguagesW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
    module = kernel.LoadLibraryExW(str(path), None, LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE)
    if not module:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        names = []
        name_callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t)

        def collect_name(_module, _kind, name, _parameter):
            names.append(name if name <= 0xFFFF else ctypes.wstring_at(name))
            return True

        name_callback = name_callback_type(collect_name)
        if not kernel.EnumResourceNamesW(module, _resource(RT_GROUP_ICON), name_callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        result = []
        language_callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ushort, ctypes.c_ssize_t)
        for name in names:
            languages = []

            def collect_language(_module, _kind, _name, language, _parameter):
                languages.append(language)
                return True

            language_callback = language_callback_type(collect_language)
            if not kernel.EnumResourceLanguagesW(module, _resource(RT_GROUP_ICON), _resource(name), language_callback, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            result.extend((name, language) for language in languages)
        if not result:
            raise YitoError('Windows Terminal no contiene recursos de icono')
        return result
    finally:
        kernel.FreeLibrary(module)


def _replace_icons(executable, icon):
    groups = _names_and_languages(executable)
    frames = _ico_frames(icon)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.BeginUpdateResourceW.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
    kernel.BeginUpdateResourceW.restype = ctypes.c_void_p
    kernel.UpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ushort, ctypes.c_void_p, ctypes.c_uint32]
    kernel.EndUpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    handle = kernel.BeginUpdateResourceW(str(executable), False)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    committed = False
    buffers = []
    try:
        for name, language in groups:
            entries = []
            for index, (metadata, image) in enumerate(frames):
                resource_id = 0x7000 + index
                buffer = ctypes.create_string_buffer(image)
                buffers.append(buffer)
                if not kernel.UpdateResourceW(handle, _resource(RT_ICON), _resource(resource_id), language, buffer, len(image)):
                    raise ctypes.WinError(ctypes.get_last_error())
                entries.append(struct.pack('<BBBBHHIH', *metadata, len(image), resource_id))
            group = struct.pack('<HHH', 0, 1, len(entries)) + b''.join(entries)
            buffer = ctypes.create_string_buffer(group)
            buffers.append(buffer)
            if not kernel.UpdateResourceW(handle, _resource(RT_GROUP_ICON), _resource(name), language, buffer, len(group)):
                raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.EndUpdateResourceW(handle, False):
            raise ctypes.WinError(ctypes.get_last_error())
        committed = True
    finally:
        if not committed:
            kernel.EndUpdateResourceW(handle, True)


def ensure_branded_executable(root):
    folder = root / 'core/tools/windows-terminal'
    source = folder / 'WindowsTerminal.exe'
    icon = root / 'core/assets/branding/mito.ico'
    branded = folder / 'YitoTerminal.exe'
    if os.name != 'nt' or not source.is_file() or not icon.is_file():
        return source
    identity = {
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'icon_sha256': hashlib.sha256(icon.read_bytes()).hexdigest(),
    }
    marker = folder / '.mito-branding.json'
    if branded.is_file() and marker.is_file() and read_json(marker) == identity:
        return branded
    temporary = folder / f'.mito-terminal-{uuid.uuid4().hex}.exe'
    try:
        shutil.copy2(source, temporary)
        _replace_icons(temporary, icon)
        temporary.replace(branded)
        write_json(marker, identity)
    finally:
        temporary.unlink(missing_ok=True)
    return branded
