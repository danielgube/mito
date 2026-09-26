"""Explicit taskbar identity for this instance's private Terminal windows."""
import ctypes
from ctypes import wintypes as W
import hashlib
import os
import subprocess
import uuid


class GUID(ctypes.Structure):
    _fields_ = [('a', W.DWORD), ('b', W.WORD), ('c', W.WORD), ('d', ctypes.c_ubyte * 8)]


class PropertyKey(ctypes.Structure):
    _fields_ = [('fmtid', GUID), ('pid', W.DWORD)]


class VariantData(ctypes.Union):
    _fields_ = [('text', ctypes.c_void_p), ('storage', ctypes.c_uint64 * 2)]


class PropVariant(ctypes.Structure):
    _fields_ = [('vt', W.WORD), ('reserved', W.WORD * 3), ('data', VariantData)]


def guid(value):
    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def identity_properties(root):
    root = root.resolve()
    tag = hashlib.sha256(str(root).casefold().encode()).hexdigest()[:24]
    argv = [str(root / 'core/tools/python/pythonw.exe'), '-B', '-I', '-X', 'utf8', '-m', 'mito', 'start']
    # Relaunch properties must precede the explicit ID. All tabs share one identity.
    return [(2, subprocess.list2cmdline(argv)), (4, 'MITO'),
            (3, str(root / 'core/assets/branding/mito.ico') + ',0'), (5, 'MITO.Instance.' + tag)]


def terminal_windows(root):
    """Match the full executable path and Terminal class, never its mutable title."""
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user.EnumWindows.argtypes = [callback_type, W.LPARAM]
    user.EnumWindows.restype = W.BOOL
    user.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user.GetClassNameW.restype = ctypes.c_int
    user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user.GetWindowThreadProcessId.restype = W.DWORD
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = W.BOOL
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.CloseHandle.restype = W.BOOL
    folder = root.resolve() / 'core/tools/windows-terminal'
    allowed = {str(folder / name).casefold() for name in ('YitoTerminal.exe', 'WindowsTerminal.exe')}
    result = []

    @callback_type
    def collect(hwnd, _):
        name = ctypes.create_unicode_buffer(256)
        if not user.GetClassNameW(hwnd, name, len(name)) or name.value != 'CASCADIA_HOSTING_WINDOW_CLASS':
            return True
        pid = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = kernel.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not process:
            return True
        try:
            path = ctypes.create_unicode_buffer(32768)
            size = W.DWORD(len(path))
            if kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)) and path.value.casefold() in allowed:
                result.append(hwnd)
        finally:
            kernel.CloseHandle(process)
        return True

    if not user.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def set_window_properties(hwnd, properties):
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    iid = guid('886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99')  # IPropertyStore
    fmtid = guid('9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3')
    store = ctypes.c_void_p()
    shell.SHGetPropertyStoreForWindow.argtypes = [W.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetPropertyStoreForWindow.restype = ctypes.c_long
    hr = shell.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(store))
    if hr < 0:
        raise ctypes.WinError(hr)
    table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = ctypes.WINFUNCTYPE(W.ULONG, ctypes.c_void_p)(table[2])
    set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                 ctypes.POINTER(PropertyKey), ctypes.POINTER(PropVariant))(table[6])
    try:
        for pid, value in properties:
            buffer = ctypes.create_unicode_buffer(value)
            variant = PropVariant()
            variant.vt = 31  # VT_LPWSTR; SetValue copies the buffer synchronously.
            variant.data.text = ctypes.addressof(buffer)
            key = PropertyKey(fmtid, pid)
            hr = set_value(store, ctypes.byref(key), ctypes.byref(variant))
            if hr < 0:
                raise ctypes.WinError(hr)
        # Window stores apply immediately; Commit has no effect.
    finally:
        release(store)


def apply(root):
    """Called once by each owned child, after Terminal has created its window."""
    if os.name != 'nt' or not (root / 'core/assets/branding/mito.ico').is_file():
        return 0
    ole = ctypes.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, W.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    hr = ole.CoInitializeEx(None, 2)
    if hr < 0 and hr != -2147417850:  # RPC_E_CHANGED_MODE: caller already initialized COM.
        raise ctypes.WinError(hr)
    try:
        windows = terminal_windows(root)
        properties = identity_properties(root)
        for hwnd in windows:
            set_window_properties(hwnd, properties)
        return len(windows)
    finally:
        if hr >= 0:
            ole.CoUninitialize()


def apply_safely(root):
    from . import diagnostics
    try:
        count = apply(root)
        diagnostics.write(root, 'info', 'terminal.identity', 'Taskbar identity applied', windows=count)
    except OSError as exc:
        diagnostics.write(root, 'warning', 'terminal.identity', str(exc))
