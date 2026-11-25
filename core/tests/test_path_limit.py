"""La raíz portable debe quedar cerca de la del disco sin LongPathsEnabled."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mito import path_limit
from mito.__main__ import main

BOOTSTRAP = Path(__file__).resolve().parents[1] / 'bootstrap.ps1'


class PathLimitTests(unittest.TestCase):
    def test_short_roots_pass_and_long_roots_only_fail_without_long_paths(self):
        self.assertIsNone(path_limit.problem('C:\\mito', enabled=lambda: False))
        self.assertIsNone(path_limit.problem('C:\\mito-0.3.1-windows-x64\\', enabled=lambda: False))
        root = 'C:\\' + 'x' * (path_limit.MAX_ROOT - 3)
        self.assertEqual(len(root), path_limit.MAX_ROOT)
        self.assertIsNone(path_limit.problem(root, enabled=lambda: False))
        downloads = 'C:\\Users\\usuario\\Downloads\\mito-0.3.1-windows-x64'
        message = path_limit.problem(downloads, enabled=lambda: False)
        self.assertIn(downloads, message)
        self.assertIn(str(len(downloads)), message)
        self.assertIsNone(path_limit.problem(downloads, enabled=lambda: True))

    def test_manager_and_bootstrap_are_blocked_but_other_commands_are_not(self):
        for command in (['ui'], ['start'], ['bootstrap']):
            with patch('mito.path_limit.problem', return_value='ruta larga'), \
                    patch('builtins.input', return_value='') as pause, \
                    patch('mito.engine.Engine.initialize') as initialize:
                self.assertEqual(main(command), 1)
                pause.assert_called_once()
                initialize.assert_not_called()
        with patch('mito.path_limit.problem', return_value='ruta larga') as check:
            self.assertEqual(main(['logs']), 0)
            check.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5.1 bootstrap')
    def test_bootstrap_uses_the_same_threshold(self):
        functions = BOOTSTRAP.read_text(encoding='utf-8-sig').split("$ErrorActionPreference = 'Stop'", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'path-check.ps1'
            script.write_text('[Console]::OutputEncoding = New-Object Text.UTF8Encoding\n' + functions + f'''
$long = 'C:\\Users\\usuario\\Downloads\\mito-0.3.1-windows-x64'
$exact = 'C:\\' + ('x' * {path_limit.MAX_ROOT - 3})
@(
  [bool](Get-YitoRootPathProblem $long {path_limit.MAX_ROOT} $false),
  [bool](Get-YitoRootPathProblem $long {path_limit.MAX_ROOT} $true),
  [bool](Get-YitoRootPathProblem $exact {path_limit.MAX_ROOT} $false),
  [bool](Get-YitoRootPathProblem 'C:\\mito\\' {path_limit.MAX_ROOT} $false)
) -join ','
''', encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                     '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                    capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip().splitlines()[-1], 'True,False,False,False')
        self.assertIn('[int]$Maximum = ' + str(path_limit.MAX_ROOT), functions)


if __name__ == '__main__':
    unittest.main()
