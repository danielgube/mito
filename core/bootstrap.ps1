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

function Test-YitoLongPaths {
    try {
        $value = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -ErrorAction Stop
        return ([int]$value.LongPathsEnabled -eq 1)
    } catch { return $false }
}

# Mismo contrato que mito/path_limit.py: sin rutas largas en Windows, la raíz de
# MITO debe estar cerca de la del disco para que quepan árboles profundos.
function Get-YitoRootPathLimit {
    # MITO_ROOT_PATH_LIMIT sólo amplía el umbral (pruebas en carpetas temporales).
    $value = 0
    if ([int]::TryParse([string]$env:MITO_ROOT_PATH_LIMIT, [ref]$value) -and $value -gt 32) { return $value }
    return 32
}

function Get-YitoRootPathProblem([string]$Root, [int]$Maximum = (Get-YitoRootPathLimit), [bool]$LongPaths = (Test-YitoLongPaths)) {
    $full = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    if ($full.Length -le $Maximum -or $LongPaths) { return $null }
    return ('La carpeta de MITO tiene una ruta demasiado larga (' + $full.Length + ' caracteres; máximo ' + $Maximum + '): ' + $full +
        "`nWindows limita las rutas a 260 caracteres y algunas aplicaciones no se podrían instalar." +
        "`nMueve la carpeta más cerca de la raíz del disco, por ejemplo C:\mito, y vuelve a ejecutar mito.cmd.")
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

# --- Proxy: detección del sistema (manual, PAC o WPAD) y portal de autenticación ---
# Python no evalúa PAC; el bootstrap resuelve el proxy con Windows y lo entrega
# a Python mediante config.properties (si el usuario acepta) o variables de entorno.

function Test-YitoHttpUrl([string]$Url, [string]$Label = 'Proxy') {
    $uri = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -notin @('http','https') -or -not $uri.Host -or $uri.UserInfo) {
        throw ($Label + ': usa una URL HTTP/HTTPS sin credenciales')
    }
    return $uri
}

function ConvertTo-YitoNoProxy([string]$Override) {
    # ProxyOverride de Windows a no_proxy de Python: sólo sufijos y nombres exactos.
    $items = New-Object Collections.Generic.List[string]
    foreach ($default in @('localhost', '127.0.0.1', '::1')) { $items.Add($default) }
    foreach ($entry in ($Override -split '[;,\s]+')) {
        $entry = $entry.Trim().ToLowerInvariant()
        if (-not $entry -or $entry -eq '<local>') { continue }
        if ($entry.StartsWith('*.')) { $entry = $entry.Substring(1) }
        if ($entry.Contains('*') -or $entry.Contains('/')) { continue }
        if (-not $items.Contains($entry)) { $items.Add($entry) }
    }
    return ($items -join ',')
}

function Get-YitoSystemProxy([Uri]$Target, $Resolver = $null, $Registry = $null) {
    if ($null -eq $Resolver) { $Resolver = [Net.WebRequest]::GetSystemWebProxy() }
    if ($null -eq $Registry) {
        try { $Registry = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop }
        catch { $Registry = @{} }
    }
    if ($Resolver.IsBypassed($Target)) { return $null }
    $proxy = $Resolver.GetProxy($Target)
    if ($null -eq $proxy -or $proxy.AbsoluteUri -eq $Target.AbsoluteUri -or $proxy.Scheme -notin @('http','https')) { return $null }
    $source = 'autodetección WPAD'
    if ($Registry.ProxyEnable -eq 1 -and $Registry.ProxyServer) { $source = 'configuración manual de Windows' }
    elseif ($Registry.AutoConfigURL) { $source = 'script PAC ' + $Registry.AutoConfigURL }
    return @{
        Url = ('{0}://{1}:{2}' -f $proxy.Scheme, $proxy.Host, $proxy.Port)
        Source = $source
        NoProxy = (ConvertTo-YitoNoProxy ([string]$Registry.ProxyOverride))
    }
}

function Invoke-YitoHttp([string]$Url, [string]$ProxyUrl, [bool]$DefaultCredentials = $false, $Cookies = $null, $Body = $null) {
    # Nunca sigue redirecciones: un portal no debe confundirse con Internet ni recibir reenvíos.
    $result = @{ Url = $Url; Status = 0; Error = ''; Authenticate = ''; Location = ''; Content = '' }
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.AllowAutoRedirect = $false
    $request.Timeout = 8000
    $request.ReadWriteTimeout = 8000
    $request.UserAgent = 'MITO-bootstrap'
    if ($ProxyUrl) {
        $proxy = New-Object Net.WebProxy($ProxyUrl)
        $proxy.UseDefaultCredentials = $DefaultCredentials
        $request.Proxy = $proxy
    } else { $request.Proxy = $null }
    if ($Cookies) { $request.CookieContainer = $Cookies }
    $response = $null
    try {
        if ($null -ne $Body) {
            $request.Method = 'POST'
            $request.ContentType = 'application/x-www-form-urlencoded'
            $bytes = [Text.Encoding]::UTF8.GetBytes([string]$Body)
            $request.ContentLength = $bytes.Length
            $stream = $request.GetRequestStream()
            try { $stream.Write($bytes, 0, $bytes.Length) }
            finally { $stream.Dispose(); [Array]::Clear($bytes, 0, $bytes.Length) }
        }
        $response = $request.GetResponse()
    } catch {
        $exception = $_.Exception
        while ($exception -and -not ($exception -is [Net.WebException])) { $exception = $exception.InnerException }
        if (-not $exception) { $result.Error = $_.Exception.Message; return $result }
        $response = $exception.Response
        if (-not $response) { $result.Error = [string]$exception.Status; return $result }
    }
    try {
        $result.Status = [int]$response.StatusCode
        $values = $response.Headers.GetValues('Proxy-Authenticate')
        if ($values) { $result.Authenticate = (@($values | ForEach-Object { ($_.Trim() -split '\s+', 2)[0] }) | Select-Object -Unique) -join ', ' }
        $result.Location = [string]$response.Headers['Location']
        try {
            $reader = New-Object IO.StreamReader($response.GetResponseStream(), [Text.Encoding]::UTF8)
            try {
                $buffer = New-Object char[] 1048576
                $count = $reader.ReadBlock($buffer, 0, $buffer.Length)
                $result.Content = [string]::new($buffer, 0, $count)
            } finally { $reader.Dispose() }
        } catch { }
    } finally { $response.Close() }
    return $result
}

function Get-YitoProxyKind($Probe, [int]$Expected) {
    if ($Probe.Status -eq $Expected) { return 'ok' }
    if ($Probe.Status -eq 407) { return 'auth' }
    if ($Probe.Status -in @(301, 302, 303, 307, 308) -and $Probe.Location) { return 'portal' }
    if ($Probe.Status -eq 0) { return 'unreachable' }
    return 'blocked'
}

function Get-YitoHtmlAttributes([string]$Tag) {
    $attributes = @{}
    foreach ($match in [regex]::Matches($Tag, '([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|''([^'']*)''|([^\s"''>]+))')) {
        $value = if ($match.Groups[2].Success) { $match.Groups[2].Value } elseif ($match.Groups[3].Success) { $match.Groups[3].Value } else { $match.Groups[4].Value }
        $attributes[$match.Groups[1].Value.ToLowerInvariant()] = [Net.WebUtility]::HtmlDecode($value)
    }
    return $attributes
}

function Get-YitoPortalForm([string]$Html, [string]$PasswordField = 'password') {
    # Primer formulario con contraseña, como network.Form: conserva campos ocultos.
    foreach ($form in [regex]::Matches([string]$Html, '(?is)<form\b([^>]*)>(.*?)</form>')) {
        $fields = [ordered]@{}
        $user = ''
        $password = ''
        foreach ($field in [regex]::Matches($form.Groups[2].Value, '(?is)<input\b[^>]*>')) {
            $attributes = Get-YitoHtmlAttributes $field.Value
            $name = $attributes['name']
            if (-not $name) { continue }
            $type = if ($attributes['type']) { $attributes['type'].ToLowerInvariant() } else { 'text' }
            $fields[$name] = if ($attributes.ContainsKey('value')) { $attributes['value'] } else { '' }
            if ($type -eq 'password' -or $name -eq $PasswordField) { if (-not $password) { $password = $name } }
            elseif (-not $user -and $type -in @('text', 'email')) { $user = $name }
        }
        if ($password) {
            return @{ Action = [string](Get-YitoHtmlAttributes $form.Groups[1].Value)['action']; Fields = $fields; UserField = $user; PasswordField = $password }
        }
    }
    return $null
}

function Set-YitoProperties([string]$Path, $Values) {
    # Sustituye o añade claves conservando comentarios, orden y salto de línea.
    $text = ''
    if ([IO.File]::Exists($Path)) { $text = [IO.File]::ReadAllText($Path) }
    $newline = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lines = New-Object Collections.Generic.List[string]
    if ($text) { foreach ($line in ($text -split '\r?\n')) { $lines.Add($line) } }
    while ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') { $lines.RemoveAt($lines.Count - 1) }
    foreach ($key in @($Values.Keys)) {
        $line = $key + '=' + $Values[$key]
        $index = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -cmatch ('^\s*' + [regex]::Escape($key) + '\s*=')) { $index = $i; break }
        }
        if ($index -ge 0) { $lines[$index] = $line } else { $lines.Add($line) }
    }
    $temp = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temp, (($lines -join $newline) + $newline), (New-Object Text.UTF8Encoding($false)))
        # PowerShell convierte $null en '' para parámetros string de .NET; NullString evita la copia de seguridad.
        if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temp, $Path, [NullString]::Value) } else { [IO.File]::Move($temp, $Path) }
    } finally {
        # Un fallo no debe dejar copias de config.properties junto a la instancia.
        if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
    }
}

function Read-YitoYes([string]$Prompt, [bool]$Default) {
    try { $answer = Read-Host $Prompt } catch { return $false }
    if (-not $answer -or -not $answer.Trim()) { return $Default }
    return $answer.Trim().ToLowerInvariant() -in @('s', 'si', 'sí', 'y', 'yes')
}

function Invoke-YitoPortalLogin([string]$PortalUrl, [string]$ProxyUrl, [bool]$DefaultCredentials, [string]$User, [Security.SecureString]$Password, [string]$CheckUrl, [int]$Expected) {
    # Mismo contrato que network.Network.authenticate: cookies efímeras, mismo origen,
    # HTTPS salvo exclusión explícita y comprobación final sin cookies.
    $settings = $script:YitoSettings
    $userField = if ($settings['proxy_authentication_username_field']) { $settings['proxy_authentication_username_field'] } else { 'username' }
    $passwordField = if ($settings['proxy_authentication_password_field']) { $settings['proxy_authentication_password_field'] } else { 'password' }
    $base = Test-YitoHttpUrl $PortalUrl 'Portal'
    $cookies = New-Object Net.CookieContainer
    $page = Invoke-YitoHttp $base.AbsoluteUri $ProxyUrl $DefaultCredentials $cookies
    if ($page.Status -lt 200 -or $page.Status -ge 300) { return $false }
    $form = Get-YitoPortalForm $page.Content $passwordField
    $action = $base
    $fields = [ordered]@{}
    if ($form) {
        if ($form.Action) { $action = New-Object Uri($base, $form.Action) }
        $fields = $form.Fields
    }
    [void](Test-YitoHttpUrl $action.AbsoluteUri 'Portal')
    if ($action.Scheme -ne $base.Scheme -or $action.Authority -ne $base.Authority) { throw 'El formulario apunta a otro origen; configura su URL explícitamente' }
    if ($action.Scheme -ne 'https' -and $settings['proxy_authentication_require_https'] -ne 'false') {
        throw 'El POST requiere HTTPS; configura proxy_authentication_require_https=false para un portal HTTP'
    }
    $prefix = 'proxy_authentication_form_field.'
    foreach ($key in @($settings.Keys)) { if ($key.StartsWith($prefix)) { $fields[$key.Substring($prefix.Length)] = $settings[$key] } }
    $fields[$userField] = $User
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    $body = $null
    try {
        $fields[$passwordField] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        $body = (@($fields.Keys) | ForEach-Object { [Uri]::EscapeDataString([string]$_) + '=' + [Uri]::EscapeDataString([string]$fields[$_]) }) -join '&'
        $post = Invoke-YitoHttp $action.AbsoluteUri $ProxyUrl $DefaultCredentials $cookies $body
    } finally {
        $fields[$passwordField] = $null
        $body = $null
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    if (($post.Status -lt 200 -or $post.Status -ge 300) -and $post.Status -notin @(301, 302, 303)) { return $false }
    $check = Invoke-YitoHttp $CheckUrl $ProxyUrl $DefaultCredentials
    return ($check.Status -eq $Expected)
}

function Initialize-YitoProxy([Uri]$Target, [bool]$Interactive, [string]$ConfigPath) {
    # Devuelve los argumentos de proxy para Invoke-WebRequest. config.properties manda;
    # sin configuración se detecta el proxy de Windows y sólo se guarda si se acepta.
    $settings = $script:YitoSettings
    $checkUrl = if ($settings['network.check_url']) { $settings['network.check_url'] } else { 'https://www.gstatic.com/generate_204' }
    $expected = if ($settings['network.check_status']) { [int]$settings['network.check_status'] } else { 204 }
    [void](Test-YitoHttpUrl $checkUrl 'network.check_url')
    $configured = $settings.ContainsKey('https_proxy') -or $settings.ContainsKey('http_proxy')
    $noProxy = ''
    if ($configured) {
        if (-not $settings.ContainsKey('https_proxy')) { return @{} }
        $proxyUrl = $settings['https_proxy']
        if (-not $proxyUrl) {
            # Valor vacío: sin proxy, también frente al proxy heredado de Windows.
            [Net.WebRequest]::DefaultWebProxy = $null
            Write-YitoDiagnostic 'info' 'proxy' 'Proxy disabled by config.properties'
            return @{}
        }
        [void](Test-YitoHttpUrl $proxyUrl 'Proxy')
        $source = 'config.properties'
    } else {
        $found = $null
        $environmentProxy = [Environment]::GetEnvironmentVariable('HTTPS_PROXY')
        if ($environmentProxy) {
            try {
                [void](Test-YitoHttpUrl $environmentProxy 'Proxy')
                $found = @{ Url = $environmentProxy.TrimEnd('/'); Source = 'variable de entorno HTTPS_PROXY'; NoProxy = (ConvertTo-YitoNoProxy ([string]$env:NO_PROXY)) }
            } catch { Write-YitoDiagnostic 'warning' 'proxy' 'Ignoring HTTPS_PROXY with credentials or invalid URL' }
        }
        if (-not $found) { $found = Get-YitoSystemProxy $Target }
        if (-not $found) {
            Write-YitoDiagnostic 'info' 'proxy' 'No system proxy detected; direct connection'
            return @{}
        }
        $proxyUrl = $found.Url
        $noProxy = $found.NoProxy
        $source = $found.Source
        Write-Host ('MITO: proxy detectado: ' + $proxyUrl + ' (' + $source + ')') -ForegroundColor Cyan
    }

    $defaultCredentials = $false
    $probe = Invoke-YitoHttp $checkUrl $proxyUrl
    $kind = Get-YitoProxyKind $probe $expected
    if ($kind -ne 'ok' -and ([Uri]$checkUrl).Scheme -eq 'https') {
        # Un portal sólo puede redirigir peticiones HTTP; HTTPS falla en el túnel.
        $builder = New-Object UriBuilder($checkUrl)
        $builder.Scheme = 'http'
        $builder.Port = -1
        $plain = Invoke-YitoHttp $builder.Uri.AbsoluteUri $proxyUrl
        $plainKind = Get-YitoProxyKind $plain $expected
        if ($plainKind -in @('auth', 'portal')) { $probe = $plain; $kind = $plainKind }
    }
    if ($kind -eq 'auth' -and $probe.Authenticate -match '(?i)negotiate|ntlm') {
        $retry = Invoke-YitoHttp $checkUrl $proxyUrl $true
        if ((Get-YitoProxyKind $retry $expected) -eq 'ok') { $defaultCredentials = $true; $kind = 'windows' }
    }
    $portalUrl = ''
    if ($kind -eq 'portal') {
        try { $portalUrl = (Test-YitoHttpUrl (New-Object Uri((New-Object Uri($probe.Url)), $probe.Location)).AbsoluteUri 'Portal').AbsoluteUri }
        catch { $kind = 'blocked' }
    }
    Write-YitoDiagnostic 'info' 'proxy' ('Proxy ' + $proxyUrl + ' from ' + $source + ': ' + $kind) @{status=$probe.Status; authenticate=$probe.Authenticate; network_error=$probe.Error}
    switch ($kind) {
        'ok' { Write-Host 'MITO: el proxy permite el acceso a Internet.' }
        'windows' { Write-Host 'MITO: el proxy acepta tu sesión de Windows (NTLM/Kerberos). Python no puede usarla: algunas descargas podrían fallar con HTTP 407.' -ForegroundColor Yellow }
        'auth' { Write-Host ('MITO: el proxy exige autenticación HTTP 407 (' + $probe.Authenticate + '), que MITO no implementa.') -ForegroundColor Yellow }
        'portal' { Write-Host ('MITO: el proxy redirige a un portal de autenticación: ' + (Protect-YitoText $portalUrl)) -ForegroundColor Yellow }
        'blocked' { Write-Host ('MITO: la comprobación de Internet a través del proxy respondió HTTP ' + $probe.Status + '.') -ForegroundColor Yellow }
        default { Write-Host ('MITO: no se pudo comprobar Internet a través del proxy (' + $probe.Error + ').') -ForegroundColor Yellow }
    }

    if (-not $configured) {
        $values = [ordered]@{ http_proxy = $proxyUrl; https_proxy = $proxyUrl; no_proxy = $noProxy }
        if ($portalUrl -and -not $settings['proxy_authentication_url']) {
            $values['enable_proxy_authentication'] = 'true'
            $values['proxy_authentication_url'] = $portalUrl
            $page = Invoke-YitoHttp $portalUrl $proxyUrl
            $form = Get-YitoPortalForm $page.Content
            # Son nombres de campos HTML, no credenciales; sólo se guardan si no son los predeterminados.
            if ($form) {
                if ($form.UserField -and $form.UserField -ne 'username') { $values['proxy_authentication_username_field'] = $form.UserField }
                if ($form.PasswordField -ne 'password') { $values['proxy_authentication_password_field'] = $form.PasswordField }
            }
        }
        foreach ($key in @($values.Keys)) { $settings[$key] = $values[$key] }
        $saved = $false
        if ($Interactive) {
            Write-Host ''
            Write-Host 'Se añadirán estas claves a config.properties:'
            foreach ($key in @($values.Keys)) { Write-Host ('  ' + $key + '=' + (Protect-YitoText ([string]$values[$key]))) }
            if ($values.Contains('proxy_authentication_url')) {
                Write-Host 'Tu usuario y contraseña nunca se guardan: MITO los pedirá cuando el portal lo requiera.'
            }
            if (Read-YitoYes '¿Guardar esta configuración de red? [S/n]' $true) {
                try {
                    Set-YitoProperties $ConfigPath $values
                    $saved = $true
                    Write-Host 'MITO: configuración de red guardada en config.properties.'
                    Write-YitoDiagnostic 'info' 'proxy' 'Detected proxy saved to config.properties'
                } catch {
                    Write-Host ('MITO: no se pudo guardar config.properties: ' + $_.Exception.Message) -ForegroundColor Yellow
                    Write-YitoDiagnostic 'warning' 'proxy' $_.Exception.Message
                }
            }
        }
        if (-not $saved) {
            Write-Host 'MITO: se usará el proxy detectado sólo durante esta preparación.'
            # Python lee estas variables mientras config.properties no declare proxy.
            $env:HTTP_PROXY = $proxyUrl
            $env:HTTPS_PROXY = $proxyUrl
            $env:NO_PROXY = $noProxy
        }
    }

    $authenticationEnabled = $settings['enable_proxy_authentication'] -eq 'true' -and $settings['proxy_authentication_url']
    if ($Interactive -and $authenticationEnabled -and $kind -notin @('ok', 'windows')) {
        Write-Host ''
        Write-Host 'Inicia sesión en el portal de red para continuar. Deja el usuario vacío para omitirlo.'
        for ($attempt = 0; $attempt -lt 3; $attempt++) {
            try { $user = Read-Host 'Usuario' } catch { break }
            if (-not $user -or -not $user.Trim()) { break }
            $password = Read-Host 'Contraseña' -AsSecureString
            try {
                $authenticated = Invoke-YitoPortalLogin $settings['proxy_authentication_url'] $proxyUrl $defaultCredentials $user.Trim() $password $checkUrl $expected
            } catch {
                Write-Host ('MITO: ' + $_.Exception.Message) -ForegroundColor Red
                Write-YitoDiagnostic 'warning' 'proxy-authentication' $_.Exception.Message
                break
            } finally { $password.Dispose(); $user = $null }
            if ($authenticated) {
                Write-Host 'MITO: autenticación correcta.' -ForegroundColor Green
                Write-YitoDiagnostic 'info' 'proxy-authentication' 'Portal authentication verified'
                break
            }
            Write-Host 'MITO: no se pudo verificar Internet. Revisa usuario y contraseña.' -ForegroundColor Yellow
            Write-YitoDiagnostic 'warning' 'proxy-authentication' 'Portal authentication not verified'
        }
    }

    $arguments = @{ Proxy = $proxyUrl }
    if ($defaultCredentials) { $arguments.ProxyUseDefaultCredentials = $true }
    return $arguments
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
if (-not $Approved) {
    # El actualizador (-Approved) prepara en una carpeta temporal del host, no en la instancia.
    $pathProblem = Get-YitoRootPathProblem $root
    if ($pathProblem) {
        Write-YitoDiagnostic 'error' 'path-limit' $pathProblem
        Write-Host ''
        Write-Host ('MITO: ' + $pathProblem) -ForegroundColor Red
        Write-Host ''
        try { [void](Read-Host 'Pulsa Intro para cerrar') } catch {}
        exit 3
    }
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
    $phase = 'proxy'
    # -Approved (actualizador sin consola) nunca pregunta: usa el proxy sólo en esta ejecución.
    $proxyArgs = Initialize-YitoProxy ([Uri]$lock.python.url) (-not $Approved) (Join-Path $root 'config.properties')
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
