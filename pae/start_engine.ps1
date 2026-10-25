# Start the OpenGloss engine from this folder using the BUNDLED python.exe.
#
# Why this file exists (2026-10-25): the original start_engine.bat resolved
# 'python' from PATH and polled /v1/health with Invoke-WebRequest. On a machine
# without Python installed, or behind a system proxy, that produced a silent
# 120 second timeout with no diagnostic at all. This version always uses the
# python.exe that ships in this folder, disables the proxy for the loopback
# health check, and prints the engine log when the engine does not come up.
param([int]$Port = 4815)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here

$py = Join-Path $here 'python.exe'
if (-not (Test-Path -LiteralPath $py)) {
    Write-Host '[PAE] ERROR: python.exe is missing next to this script.'
    Write-Host '        Re-extract the complete ZIP and run Install.cmd again.'
    exit 1
}

$mine = Join-Path $here 'pae.db'
$url  = 'http://127.0.0.1:' + $Port + '/v1/health'

function Read-Health([string]$u) {
    try {
        $wc = New-Object System.Net.WebClient
        $wc.Proxy = $null                      # never route loopback through a proxy
        $wc.Encoding = [System.Text.Encoding]::UTF8
        return ($wc.DownloadString($u) | ConvertFrom-Json)
    } catch {
        return $null
    }
}

# 1) Is an engine already serving this folder?
$h = Read-Health $url
if ($h -and $h.ok -and $h.db) {
    if ($h.db.ToString().ToLower() -eq $mine.ToLower()) {
        Write-Host '[PAE] engine already running for this folder.'
        Write-Host ('[PAE] engine ready: ' + $url)
        exit 0
    }
    Write-Host ('[PAE] ERROR: port ' + $Port + ' is held by an engine using a DIFFERENT database.')
    Write-Host ('        it reports: ' + $h.db)
    Write-Host ('        this folder: ' + $mine)
    Write-Host '        Stop that one first (stop_engine.bat), or start this one on another port.'
    exit 1
}

# 2) Start it with the bundled interpreter and capture everything it says.
$log  = Join-Path $here 'engine-start.log'
$elog = Join-Path $here 'engine-start.err.log'
Write-Host ('[PAE] starting the engine with the bundled Python: ' + $py)
Write-Host '[PAE] first start loads a 65 MB dictionary; a slow machine can take a while.'
$proc = Start-Process -FilePath $py `
    -ArgumentList '-m','uvicorn','pae_core.api:app','--port',"$Port" `
    -WorkingDirectory $here -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError $elog

# 3) Wait for readiness.
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$next = 10
$ok = $false
$died = $false
while ($sw.Elapsed.TotalSeconds -lt 180) {
    $h = Read-Health $url
    if ($h -and $h.ok -and $h.db -and ($h.db.ToString().ToLower() -eq $mine.ToLower())) { $ok = $true; break }
    if ($proc.HasExited) { $died = $true; break }
    if ($sw.Elapsed.TotalSeconds -ge $next) {
        Write-Host ('[PAE] still waiting... ' + [int]$sw.Elapsed.TotalSeconds + 's')
        $next += 10
    }
    Start-Sleep -Seconds 2
}

if ($ok) {
    Write-Host ('[PAE] engine ready: ' + $url)
    exit 0
}

Write-Host ''
Write-Host '[PAE] ===== the engine did not become ready ====='
if ($died) { Write-Host ('[PAE] the process exited with code ' + $proc.ExitCode + '.') }
else       { Write-Host '[PAE] it was still running but never answered /v1/health.' }
Write-Host ''
foreach ($f in @($elog, $log)) {
    if (Test-Path -LiteralPath $f) {
        $body = Get-Content -LiteralPath $f -Tail 40 -ErrorAction SilentlyContinue
        if ($body) {
            Write-Host ('----- ' + (Split-Path $f -Leaf) + ' -----')
            $body | ForEach-Object { Write-Host ('  ' + $_) }
            Write-Host ''
        }
    }
}
if ($proc -and -not $proc.HasExited) {
    Write-Host '[PAE] stopping the half-started engine this script launched.'
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}
Write-Host 'Common causes:'
Write-Host '  1. the ZIP was not fully extracted (should be about 548 files)'
Write-Host '  2. a leftover engine on the same port from another folder'
Write-Host '  3. antivirus blocking the bundled python.exe'
Write-Host '  4. the path is on a network drive or a read-only location'
exit 1