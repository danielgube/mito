@echo off
setlocal
if not exist "%~dp0core\tools\python\mito-ready.json" (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0core\bootstrap.ps1"
  if errorlevel 1 exit /b 1
)
if not exist "%~dp0core\state\bootstrap-tools.json" (
  "%~dp0core\tools\python\python.exe" -B -I -X utf8 -m mito.startup bootstrap
  if errorlevel 1 goto result
  if not errorlevel 0 goto result
)
if "%~1"=="" (
  "%~dp0core\tools\python\python.exe" -B -I -X utf8 -m mito.startup start
) else (
  "%~dp0core\tools\python\python.exe" -B -I -X utf8 -m mito.startup %*
)
:result
set "mito_exit=%errorlevel%"
if %mito_exit% LSS 0 goto native_failure
if %mito_exit% EQU 9009 goto native_failure
exit /b %mito_exit%
:native_failure
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0core\bootstrap.ps1" -DiagnosticExitCode %mito_exit%
exit /b %mito_exit%
