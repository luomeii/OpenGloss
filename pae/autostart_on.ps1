# Register a per-user autostart entry for THIS folder.
#
# Lessons from 2026-10-25:
#  1. The launcher must not ShellExecute the .bat directly. Windows shows
#     "Open File - Security Warning" for batch files that came out of a
#     downloaded ZIP, and at logon nobody can click Run. Running through
#     cmd.exe avoids that prompt. The installer also strips that mark.
#  2. It must use the bundled python.exe (start_engine.bat does), never a PATH lookup.
#  3. It must not block at logon: stdin comes from NUL so the trailing 'pause' returns.
#  4. The .vbs uses relative names with CurrentDirectory set, so there is not a
#     single nested quote to get wrong.
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here

$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$entryName = 'OpenGloss'
$vbsPath = Join-Path $here 'autostart_hidden.vbs'

if ($Remove) {
    if (Get-ItemProperty -Path $runKey -Name $entryName -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $runKey -Name $entryName -Force
        Write-Host '[PAE] Autostart is OFF. The entry was removed.'
    } else {
        Write-Host '[PAE] Autostart was already OFF.'
    }
    if (Test-Path -LiteralPath $vbsPath) { Remove-Item -LiteralPath $vbsPath -Force }
    exit 0
}

foreach ($need in 'python.exe','start_engine.bat') {
    if (-not (Test-Path -LiteralPath (Join-Path $here $need))) {
        Write-Host ('[PAE] ERROR: ' + $need + ' is missing next to this script.')
        Write-Host '        Re-extract the complete ZIP and run Install.cmd again.'
        exit 1
    }
}

$vbs = @(
    'Set fso = CreateObject("Scripting.FileSystemObject")',
    'Dim d, sh',
    'd = fso.GetParentFolderName(WScript.ScriptFullName)',
    'Set sh = CreateObject("WScript.Shell")',
    'sh.CurrentDirectory = d',
    'sh.Run "cmd /c start_engine.bat < nul >> autostart.log 2>&1", 0, False'
)
Set-Content -LiteralPath $vbsPath -Value $vbs -Encoding ASCII
Write-Host ('[PAE] launcher written: ' + $vbsPath)
Write-Host '[PAE] launcher content:'
Get-Content -LiteralPath $vbsPath | ForEach-Object { Write-Host ('  ' + $_) }
Write-Host ''

# Fail loudly if the generated script does not even parse.
$probe = & cscript //nologo //T:1 $vbsPath 2>&1
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) {
    Write-Host '[PAE] ERROR: the generated launcher has a script error:'
    $probe | ForEach-Object { Write-Host ('  ' + $_) }
    exit 1
}

$value = 'wscript.exe "' + $vbsPath + '"'
Set-ItemProperty -Path $runKey -Name $entryName -Value $value -Force
Write-Host ('[PAE] registered for this user: ' + $value)
Write-Host '[PAE] Autostart is ON. The engine will start (hidden) at every logon.'
Write-Host ('[PAE] Start log: ' + (Join-Path $here 'autostart.log'))
Write-Host '[PAE] Turn it off again with autostart_off.bat.'
exit 0