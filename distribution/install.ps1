# Offline, in-place setup only. Does not launch or stop any engine.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    if (-not [Environment]::Is64BitOperatingSystem -or [Environment]::OSVersion.Version.Major -lt 10) {
        throw 'Windows 10/11 64-bit is required.'
    }
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    if ($root.StartsWith('\\')) { throw 'Extract to a local writable folder first.' }
    $manifest = Get-Content -LiteralPath (Join-Path $root 'INSTALL-MANIFEST.json') -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($manifest.schema -ne 1) { throw 'Unsupported installation manifest.' }
    Write-Host '[OpenGloss] Checking offline installation files...'
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $relative = $entry.Name
        $parts = $relative.Split('/')
        if ($relative.Contains('\') -or $relative.Contains(':') -or
            @($parts | Where-Object { $_ -eq '' -or $_ -eq '.' -or $_ -eq '..' -or $_ -ne $_.TrimEnd(' ', '.') }).Count) {
            throw ('Invalid manifest path: ' + $relative)
        }
        $path = Join-Path $root $relative
        $parent = $path
        while ($parent -and $parent -ne $root) {
            if ((Test-Path -LiteralPath $parent) -and
                ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'Installation files must not be links or junctions.'
            }
            $parent = Split-Path -Parent $parent
        }
        $hash = ''
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $algorithm = [Security.Cryptography.SHA256]::Create()
            $stream = [IO.File]::OpenRead($path)
            try { $hash = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '') }
            finally { $stream.Dispose(); $algorithm.Dispose() }
        }
        if ($hash -ne $entry.Value) {
            throw ('Integrity check failed: ' + $relative + '. Extract a fresh complete ZIP; existing data was not changed.')
        }
    }
    $pae = Join-Path $root 'pae'
    $python = Join-Path $pae 'python.exe'
    $names = @('quick_start', 'quick_stop', 'autostart_on', 'autostart_off')
    $shell = New-Object -ComObject WScript.Shell
    foreach ($name in $names) {
        $link = Join-Path $root ($name + '.lnk')
        $target = Join-Path $pae ($name + '.bat')
        if ((Test-Path -LiteralPath $link) -and $shell.CreateShortcut($link).TargetPath -ne $target) {
            throw ('An unrelated shortcut already exists: ' + $name + '.lnk')
        }
    }
    # Only check runtime imports and the PUBLIC dictionary. Never import the API
    # here: doing so could create a database or start application background work.
    $probe = @'
import sys, pathlib, sqlite3, ssl
import fastapi, uvicorn, pydantic, pydantic_core
r = pathlib.Path(sys.executable).resolve().parent
for module in (fastapi, uvicorn, pydantic, pydantic_core):
    assert pathlib.Path(module.__file__).resolve().is_relative_to(r)
with sqlite3.connect((r.parent / 'resources/ECDICT/dict.sqlite').as_uri() + '?mode=ro', uri=True) as db:
    assert db.execute('SELECT COUNT(*) FROM dict_words').fetchone()[0] > 700000
    assert db.execute('SELECT COUNT(*) FROM exchange_map').fetchone()[0] > 50000
assert (r.parent / 'resources/ECDICT/lemma.en.txt').stat().st_size > 1000000
print('[OpenGloss] Bundled Python, dependencies and dictionary are ready.')
'@
    & $python -I -B -c $probe
    if ($LASTEXITCODE -ne 0) { throw 'Bundled runtime check failed. No engine was started.' }
    foreach ($name in $names) {
        $shortcut = $shell.CreateShortcut((Join-Path $root ($name + '.lnk')))
        $shortcut.TargetPath = Join-Path $pae ($name + '.bat')
        $shortcut.WorkingDirectory = $pae
        $shortcut.Save()
    }
    Write-Host ''
    Write-Host ('[OpenGloss] Installation ready in: ' + $root)
    Write-Host '[OpenGloss] Double-click quick_start.lnk to start the original engine.'
    Write-Host ('[OpenGloss] Manually load the browser extension from: ' + (Join-Path $pae 'extension'))
    Write-Host '[OpenGloss] Autostart remains OFF unless you enable it yourself.'
    Write-Host '[OpenGloss] No system PATH, registry, desktop or browser settings were changed.'
    exit 0
} catch {
    Write-Host ('[OpenGloss] ERROR: ' + $_.Exception.Message)
    exit 1
}
