# Windows PowerShell 5.1, independent of Python and the downloaded runtime.
param([int]$DiagnosticExitCode = 0, [switch]$Approved, [switch]$PreviewWelcome)

function Confirm-YitoBootstrap($Manifest) {
    Write-Host ''
    Write-Host 'Bienvenido a MITO (My Integrated Terminal Orchestrator)' -ForegroundColor Cyan
    Write-Host ''
    Write-Host 'Para preparar MITO se descargarán las siguientes herramientas:'
    Write-Host '  - Python'
    foreach ($tool in $Manifest.tools) {
        $label = switch ($tool.id) {
            '7zip' { '7-Zip' }
            'windows-terminal' { 'Windows Terminal' }
            'innounp' { 'innounp (extracción de instaladores Inno Setup)' }
            default { $tool.id }
        }
        Write-Host ('  - ' + $label + ' ' + $tool.version)
    }
    Write-Host '  - Textual, Pillow, SDK MCP y sus bibliotecas necesarias'
    Write-Host ''
    Write-Host 'Es necesaria una conexión a Internet.' -ForegroundColor Yellow
    Write-Host 'Las herramientas se guardarán dentro de esta carpeta de MITO.'
    Write-Host ''
    try { $answer = Read-Host '¿Deseas continuar? [s/N]' }
    catch { return $false }
    return $answer -and $answer.Trim().ToLowerInvariant() -in @('s', 'si', 'sí')
}

function Protect-YitoText([string]$Text) {
    $Text = [regex]::Replace($Text, '(?i)(https?://)[^\s/@]+@', '$1[redacted]@')
    $Text = [regex]::Replace($Text, '(?i)(https?://[^\s?"<>]+)\?[^\s"<>]+', '$1?[redacted]')
    $Text = [regex]::Replace($Text, '(?i)\b(?:ph[cp]_|gh[pousr]_)[A-Za-z0-9_]+', '[redacted]')
    $Text = [regex]::Replace($Text, '(?i)\bBearer\s+\S+', 'Bearer [redacted]')
    $Text = [regex]::Replace($Text, '(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s]+', '<USER>')
    $Text = [regex]::Replace($Text, '(?i)(["'']?(?:password|passwd|pwd|token|api[_-]?key|secret|authorization|cookie)["'']?\s*[:=]\s*)(?:"[^"]*"|''[^'']*''|[^\s,;]+)', '$1[redacted]')
    if ($script:YitoRoot) {
        $Text = [regex]::Replace($Text, [regex]::Escape($script:YitoRoot), '<MITO>', 'IgnoreCase')
        $Text = [regex]::Replace($Text, [regex]::Escape($script:YitoRoot.Replace('\','/')), '<MITO>', 'IgnoreCase')
    }
    if ($env:USERPROFILE) { $Text = [regex]::Replace($Text, [regex]::Escape($env:USERPROFILE), '<USER>', 'IgnoreCase') }
    if ($Text.Length -gt 12000) { $Text = $Text.Substring(0, 12000) }
    return $Text
}

function Get-YitoDigest([string]$Text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Initialize-YitoDiagnostics([string]$Root) {
    $script:YitoRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $identity = (Get-YitoDigest $script:YitoRoot.ToLowerInvariant()).Substring(0,16)
    $script:YitoLogFolders = @((Join-Path $Root 'logs'), (Join-Path ([IO.Path]::GetTempPath()) ('MITO-logs\' + $identity)))
    $script:YitoSession = [Guid]::NewGuid().ToString('N')
    $script:YitoVersion = 'unknown'
    $script:YitoSettings = @{}
    try {
        foreach ($line in [IO.File]::ReadAllLines((Join-Path $Root 'config.properties'))) {
            if ($line.Trim() -match '^([^#!][^=]*)=(.*)$') { $script:YitoSettings[$Matches[1].Trim()] = $Matches[2].Trim() }
        }
    } catch { }
    try {
        $source = [IO.File]::ReadAllText((Join-Path $Root 'core\app\mito\__init__.py'))
        if ($source -match '__version__\s*=\s*["'']([0-9]+\.[0-9]+\.[0-9]+)["'']') { $script:YitoVersion = $Matches[1] }
    } catch { }
}

function Invoke-YitoLogLock([string]$Folder, [scriptblock]$Action) {
    [void][IO.Directory]::CreateDirectory($Folder)
    $stream = [IO.File]::Open((Join-Path $Folder '.diagnostics.lock'), 'OpenOrCreate', 'ReadWrite', 'ReadWrite')
    $locked = $false
    try {
        if ($stream.Length -eq 0) { $stream.WriteByte(0); $stream.Flush() }
        for ($attempt=0; $attempt -lt 10; $attempt++) {
            try { $stream.Lock(0,1); $locked=$true; break } catch { Start-Sleep -Milliseconds 20 }
        }
        if (-not $locked) { throw 'Diagnostic lock busy' }
        & $Action
    } finally {
        if ($locked) { $stream.Unlock(0,1) }
        $stream.Dispose()
    }
}

function Write-YitoDiagnostic([string]$Level, [string]$Phase, [string]$Message, [hashtable]$Details=@{}) {
    try {
        $record = @{time=[DateTime]::UtcNow.ToString('o'); level=$Level; pid=$PID; operation='bootstrap.powershell'; phase=$Phase; message=(Protect-YitoText $Message); version=$script:YitoVersion}
        foreach ($key in $Details.Keys) { $record[$key] = $Details[$key] }
        $line = ($record | ConvertTo-Json -Depth 20 -Compress) + "`n"
        foreach ($folder in $script:YitoLogFolders) {
            try {
                Invoke-YitoLogLock $folder {
                    $target = Join-Path $folder 'mito.log'
                    if ([IO.File]::Exists($target) -and ((Get-Item -LiteralPath $target).Length + [Text.Encoding]::UTF8.GetByteCount($line) -gt 1MB)) {
                        [IO.File]::Delete($target + '.3')
                        for ($i=2; $i -ge 1; $i--) {
                            if ([IO.File]::Exists($target + '.' + $i)) { [IO.File]::Move($target + '.' + $i, $target + '.' + ($i+1)) }
                        }
                        [IO.File]::Move($target, $target + '.1')
                    }
                    [IO.File]::AppendAllText($target, $line, (New-Object Text.UTF8Encoding($false)))
                }
                $script:YitoActiveLog = Join-Path $folder 'mito.log'
                if ($folder -ne $script:YitoLogFolders[0] -and -not $script:YitoFallbackShown) {
                    $script:YitoFallbackShown = $true
                    [Console]::Error.WriteLine('MITO: log alternativo: ' + $script:YitoActiveLog)
                }
                return
            } catch { }
        }
        [Console]::Error.WriteLine('MITO: cannot write local or temporary diagnostic log.')
    } catch { }
}

function Save-YitoBootstrapError($Record, [string]$Phase) {
    $id = [Guid]::NewGuid()
    $message = Protect-YitoText $Record.Exception.Message
    $trace = Protect-YitoText $Record.ScriptStackTrace
    Write-YitoDiagnostic 'error' $Phase $message @{event_id=$id.ToString(); exception=$Record.Exception.GetType().FullName; trace=$trace; windows=[Environment]::OSVersion.Version.ToString(); bits=([IntPtr]::Size*8); powershell=$PSVersionTable.PSVersion.ToString()}
    try {
        if ($script:YitoSettings['posthog.enabled'] -ne 'true') { return }
        $hostUrl = $script:YitoSettings['posthog.host'].TrimEnd('/')
        $token = $script:YitoSettings['posthog.api_key']
        if (-not $hostUrl -or -not $token) { return }
        $frame = @{platform='custom'; lang='powershell'; filename=([IO.Path]::GetFileName($Record.InvocationInfo.ScriptName)); function=$Phase; lineno=$Record.InvocationInfo.ScriptLineNumber; in_app=$true}
        $exception = @{type=$Record.Exception.GetType().Name; value=$message; mechanism=@{handled=$true; synthetic=$false}; stacktrace=@{type='raw'; frames=@($frame)}}
        $props = @{'distinct_id'=$script:YitoSession; '$process_person_profile'=$false; '$geoip_disable'=$true; '$exception_list'=@($exception); '$lib'='mito'; '$lib_version'=$script:YitoVersion; mito_version=$script:YitoVersion; mito_operation='bootstrap.powershell'; mito_phase=$Phase; mito_runtime=@{windows=[Environment]::OSVersion.Version.ToString(); bits=([IntPtr]::Size*8); powershell=$PSVersionTable.PSVersion.ToString()}}
        $event = @{uuid=$id.ToString(); timestamp=[DateTime]::UtcNow.ToString('o'); event='$exception'; properties=$props}
        $envelope = @{schema=1; destination=(Get-YitoDigest ($hostUrl + "`n" + $token)); event=$event} | ConvertTo-Json -Depth 20 -Compress
        foreach ($folder in $script:YitoLogFolders) {
            try {
                Invoke-YitoLogLock $folder {
                    $pending = Join-Path $folder 'pending'
                    [void][IO.Directory]::CreateDirectory($pending)
                    foreach ($unfinished in [IO.Directory]::GetFiles($pending, '*.tmp')) {
                        if ([IO.Path]::GetFileName($unfinished) -match '^[0-9a-f]{32}\.tmp$') { [IO.File]::Delete($unfinished) }
                    }
                    $files = @(Get-ChildItem -LiteralPath $pending -Filter '*.json' | Where-Object { $_.Name -match '^[0-9a-f]{32}\.json$' -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | Sort-Object LastWriteTimeUtc)
                    foreach ($file in $files) {
                        if ($file.LastWriteTimeUtc -lt [DateTime]::UtcNow.AddDays(-7) -or $file.Length -gt 128KB) { [IO.File]::Delete($file.FullName) }
                    }
                    $files = @($files | Where-Object { [IO.File]::Exists($_.FullName) })
                    if ($files.Count -ge 100) { foreach ($file in $files[0..($files.Count-100)]) { [IO.File]::Delete($file.FullName) } }
                    $path = Join-Path $pending ($id.ToString('N') + '.json')
                    [IO.File]::WriteAllText($path + '.tmp', $envelope, (New-Object Text.UTF8Encoding($false)))
                    [IO.File]::Move($path + '.tmp', $path)
                }
                break
            } catch { }
        }
    } catch { Write-YitoDiagnostic 'warning' 'posthog.queue' $_.Exception.Message }
}

function Send-YitoBootstrapPending {
    try {
        if ($script:YitoSettings['posthog.enabled'] -ne 'true') { return }
        $hostUrl = $script:YitoSettings['posthog.host'].TrimEnd('/')
        $token = $script:YitoSettings['posthog.api_key']
        if (-not $hostUrl -or -not $token) { return }
        $uri = [Uri]$hostUrl
        if ($uri.Scheme -notin @('http','https') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) { throw 'Invalid PostHog host' }
        $destination = Get-YitoDigest ($hostUrl + "`n" + $token)
        $sent = 0
        foreach ($folder in $script:YitoLogFolders) {
            $pending = Join-Path $folder 'pending'
            if (-not [IO.Directory]::Exists($pending)) { continue }
            foreach ($path in [IO.Directory]::GetFiles($pending, '*.json')) {
                if ($sent -ge 3) { return }
                if ([IO.Path]::GetFileName($path) -notmatch '^[0-9a-f]{32}\.json$') { continue }
                $file = Get-Item -LiteralPath $path
                if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                if ($file.Length -gt 128KB -or $file.LastWriteTimeUtc -lt [DateTime]::UtcNow.AddDays(-7)) {
                    Invoke-YitoLogLock $folder { [IO.File]::Delete($path) }
                    continue
                }
                $record = [IO.File]::ReadAllText($path) | ConvertFrom-Json
                if ($record.schema -ne 1 -or $record.destination -ne $destination -or $record.event.event -ne '$exception') { continue }
                $sent++
                $body = @{token=$token; uuid=$record.event.uuid; timestamp=$record.event.timestamp; event='$exception'; properties=$record.event.properties} | ConvertTo-Json -Depth 20 -Compress
                $request = [Net.HttpWebRequest]::Create($hostUrl + '/i/v0/e/')
                $request.Method='POST'; $request.ContentType='application/json'; $request.Timeout=3000; $request.ReadWriteTimeout=3000; $request.AllowAutoRedirect=$false
                $proxyKey = if ($uri.Scheme -eq 'https') { 'https_proxy' } else { 'http_proxy' }
                if ($script:YitoSettings.ContainsKey($proxyKey)) {
                    $proxy = $script:YitoSettings[$proxyKey]
                    if ($proxy) { $request.Proxy = New-Object Net.WebProxy($proxy) } else { $request.Proxy = $null }
                }
                $bytes = [Text.Encoding]::UTF8.GetBytes($body)
                $request.ContentLength=$bytes.Length
                try {
                    $stream=$request.GetRequestStream()
                    try { $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
                    $response=$request.GetResponse()
                    try {
                        if ([int]$response.StatusCode -lt 200 -or [int]$response.StatusCode -ge 300) { throw 'PostHog rejected event' }
                    } finally { $response.Dispose() }
                    Invoke-YitoLogLock $folder { [IO.File]::Delete($path) }
                    Write-YitoDiagnostic 'info' 'posthog' ('Event accepted: ' + $record.event.uuid)
                } catch {
                    Write-YitoDiagnostic 'warning' 'posthog' $_.Exception.Message
                    return
                } finally { $request.Abort() }
            }
        }
    } catch { Write-YitoDiagnostic 'warning' 'posthog' $_.Exception.Message }
}

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $PSScriptRoot 'tools'

if ($PreviewWelcome) {
    $previewManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'bootstrap.lock.json') -Raw | ConvertFrom-Json
    [void](Confirm-YitoBootstrap $previewManifest)
    Write-Host 'Vista previa terminada. No se ha descargado ni preparado ninguna herramienta.'
    exit 0
}

Initialize-YitoDiagnostics $root
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if ($DiagnosticExitCode -ne 0) {
    try { throw ('Python could not start or terminated abnormally. Exit code: ' + $DiagnosticExitCode) }
    catch { Save-YitoBootstrapError $_ 'runtime-native-exit' }
    Write-Host ('MITO: log: ' + $script:YitoActiveLog)
    Send-YitoBootstrapPending
    exit 1
}
$phase = 'startup'
Write-YitoDiagnostic 'info' $phase 'Bootstrap started' @{windows=[Environment]::OSVersion.Version.ToString(); os64=[Environment]::Is64BitOperatingSystem; bits=([IntPtr]::Size*8); powershell=$PSVersionTable.PSVersion.ToString()}
$guard = $null
$stage = Join-Path $tools ('bootstrap-' + [Guid]::NewGuid().ToString('N'))
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'MITO requires Windows x64' }
    $destination = Join-Path $tools 'python'
    if (-not (Test-Path -LiteralPath (Join-Path $destination 'mito-ready.json'))) {
        $phase = 'read-bootstrap-lock'
        $lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'bootstrap.lock.json') -Raw | ConvertFrom-Json
        $phase = 'confirmation'
        if (-not $Approved -and -not (Confirm-YitoBootstrap $lock)) {
            Write-YitoDiagnostic 'info' $phase 'Bootstrap cancelled before downloading'
            Write-Host 'Preparación cancelada. Puedes volver a ejecutar mito.cmd cuando quieras.'
            exit 2
        }
        Write-YitoDiagnostic 'info' $phase 'Bootstrap approved'
    }
    $phase = 'create-tools-directory'
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    $phase = 'bootstrap-lock'
    $guard = [IO.File]::Open((Join-Path $tools 'bootstrap.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $destination = Join-Path $tools 'python'
    if (Test-Path -LiteralPath (Join-Path $destination 'mito-ready.json')) {
        Write-YitoDiagnostic 'info' 'ready' 'Python already prepared'
        Send-YitoBootstrapPending
        exit 0
    }
    New-Item -ItemType Directory -Path $stage | Out-Null
    $archive = Join-Path $stage 'python.zip'
    Write-Host 'MITO: preparando Python portable, Textual y Pillow...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $proxyArgs = @{}
    $configPath = Join-Path $root 'config.properties'
    if (Test-Path -LiteralPath $configPath) {
        foreach ($line in Get-Content -LiteralPath $configPath) {
            if ($line.Trim() -match '^https_proxy\s*=(.*)$') {
                $proxyUrl = $Matches[1].Trim()
                if ($proxyUrl) {
                    $uri = [Uri]$proxyUrl
                    if ($uri.Scheme -notin @('http','https') -or $uri.UserInfo) { throw 'Proxy: usa una URL HTTP/HTTPS sin credenciales' }
                    $proxyArgs.Proxy = $proxyUrl
                } else {
                    [Net.WebRequest]::DefaultWebProxy = $null
                }
            }
        }
    }
    $phase = 'download-python'
    Write-YitoDiagnostic 'info' $phase 'Downloading pinned Python archive'
    Invoke-WebRequest -UseBasicParsing -Uri $lock.python.url -OutFile $archive @proxyArgs
    $phase = 'verify-python-hash'
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($archive)
    try { $digest = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
    if ($digest -ne $lock.python.sha256) { throw 'Hash de Python incorrecto' }
    $phase = 'extract-python'
    Write-YitoDiagnostic 'info' $phase 'Extracting verified Python archive'
    [void][Reflection.Assembly]::LoadWithPartialName('System.IO.Compression.FileSystem')
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $stage)
    Remove-Item -LiteralPath $archive
    $phase = 'run-python-bootstrap'
    Write-YitoDiagnostic 'info' $phase 'Starting private Python'
    $oldPreference = $ErrorActionPreference
    try {
        # PowerShell 5.1 treats native stderr as ErrorRecord; retain output and exit code.
        $ErrorActionPreference = 'Continue'
        & (Join-Path $stage 'python.exe') -B -I -X utf8 (Join-Path $PSScriptRoot 'bootstrap.py') --target $stage 2>&1 | ForEach-Object {
            Write-YitoDiagnostic 'info' 'python-output' ([string]$_)
            Write-Host ([string]$_)
        }
        $pythonExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $oldPreference }
    if ($pythonExit -ne 0) { throw ('No se pudo preparar el runtime. Python exit code: ' + $pythonExit) }
    $phase = 'publish-python'
    if (Test-Path -LiteralPath $destination) {
        $backup = Join-Path $tools ('python-backup-' + [Guid]::NewGuid().ToString('N'))
        if ([IO.Path]::GetFullPath($destination) -ne [IO.Path]::GetFullPath((Join-Path $root 'core\tools\python'))) { throw 'Unexpected Python destination' }
        Move-Item -LiteralPath $destination -Destination $backup
    }
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($stage)) -ne [IO.Path]::GetFullPath($tools)) { throw 'Unexpected Python staging path' }
    Move-Item -LiteralPath $stage -Destination $destination
    Write-YitoDiagnostic 'info' 'complete' 'Bootstrap completed'
    Send-YitoBootstrapPending
} catch {
    $failure = $_
    Save-YitoBootstrapError $failure $phase
    Write-Host ('MITO: ' + $failure.Exception.Message) -ForegroundColor Red
    Write-Host ('MITO: log: ' + $script:YitoActiveLog)
    Send-YitoBootstrapPending
    exit 1
} finally {
    if ($guard) { $guard.Dispose() }
    # Failed staging is retained for diagnosis, never published as a ready runtime.
}
