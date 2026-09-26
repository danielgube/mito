import ctypes
from ctypes import wintypes as W
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mito import terminal_identity as identity


class TerminalIdentityTests(unittest.TestCase):
    def test_taskbar_identity_is_per_instance_and_uses_only_mito_icon(self):
        root = Path('C:/Yito')
        properties = identity.identity_properties(root)
        values = dict(properties)
        self.assertEqual([pid for pid, _ in properties], [2, 4, 3, 5])
        self.assertTrue(values[3].endswith('core\\assets\\branding\\mito.ico,0'))
        self.assertEqual(values[4], 'MITO')
        self.assertIn('pythonw.exe', values[2])
        self.assertTrue(values[2].endswith('-m mito start'))
        self.assertNotIn('pocketool', values[2])
        self.assertEqual(values[5], dict(identity.identity_properties(Path('c:/mito')))[5])
        self.assertNotEqual(values[5], dict(identity.identity_properties(Path('C:/other-mito')))[5])

    def test_enumeration_excludes_other_instances_and_unrelated_windows(self):
        root = Path('C:/mito')
        user, kernel = MagicMock(), MagicMock()
        images = {1: r'C:\mito\core\tools\windows-terminal\YitoTerminal.exe',
                  2: r'C:\other\core\tools\windows-terminal\YitoTerminal.exe',
                  3: r'C:\host\WindowsTerminal.exe',
                  4: r'C:\mito\core\tools\windows-terminal\WindowsTerminal.exe',
                  5: r'C:\mito\core\tools\windows-terminal\YitoTerminal.exe'}

        def enum(callback, _):
            for hwnd in images:
                callback(hwnd, 0)
            return True

        def class_name(hwnd, buffer, size):
            buffer.value = 'other-class' if hwnd == 5 else 'CASCADIA_HOSTING_WINDOW_CLASS'
            return len(buffer.value)

        def pid(hwnd, pointer):
            ctypes.cast(pointer, ctypes.POINTER(W.DWORD)).contents.value = hwnd
            return 1

        def image_name(process, flags, buffer, size):
            buffer.value = images[process]
            return True

        user.EnumWindows.side_effect = enum
        user.GetClassNameW.side_effect = class_name
        user.GetWindowThreadProcessId.side_effect = pid
        kernel.OpenProcess.side_effect = lambda access, inherit, pid: pid
        kernel.QueryFullProcessImageNameW.side_effect = image_name
        with patch.object(identity.ctypes, 'WinDLL', side_effect=lambda name, **kw: user if name == 'user32' else kernel):
            self.assertEqual(identity.terminal_windows(root), [1, 4])
        self.assertEqual(kernel.CloseHandle.call_count, 4)

    def test_property_store_receives_strings_and_releases_interface(self):
        self.assertEqual(ctypes.sizeof(identity.GUID), 16)
        self.assertEqual(ctypes.sizeof(identity.PropertyKey), 20)
        self.assertEqual(ctypes.sizeof(identity.PropVariant), 24)
        recorded, released = [], []

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                           ctypes.POINTER(identity.PropertyKey), ctypes.POINTER(identity.PropVariant))
        def set_value(store, key, variant):
            self.assertEqual(variant.contents.vt, 31)
            recorded.append((key.contents.pid, ctypes.wstring_at(variant.contents.data.text)))
            return 0

        @ctypes.WINFUNCTYPE(W.ULONG, ctypes.c_void_p)
        def release(store):
            released.append(store)
            return 0

        table = (ctypes.c_void_p * 8)()
        table[2] = ctypes.cast(release, ctypes.c_void_p)
        table[6] = ctypes.cast(set_value, ctypes.c_void_p)
        table_pointer = ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p))
        instance = ctypes.pointer(table_pointer)
        shell = MagicMock()

        def get_store(hwnd, iid, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(instance, ctypes.c_void_p)
            return 0

        shell.SHGetPropertyStoreForWindow.side_effect = get_store
        properties = identity.identity_properties(Path('C:/mito'))
        with patch.object(identity.ctypes, 'WinDLL', return_value=shell):
            identity.set_window_properties(123, properties)
        self.assertEqual(recorded, properties)
        self.assertEqual(len(released), 1)

    def test_failure_to_set_identity_does_not_abort_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(identity, 'apply', side_effect=OSError('denied')), patch('mito.diagnostics.write') as log:
                identity.apply_safely(root)
            self.assertEqual(log.call_args.args[1:3], ('warning', 'terminal.identity'))
