param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$entryName = 'OpenGloss'
$vbsPath = Join-Path $PSScriptRoot 'autostart_hidden.vbs'
$expected = 'wscript.exe "' + $vbsPath + '"'

function Read-Registration {
    try {
        $key = Get-Item -LiteralPath $runKey -ErrorAction Stop
    } catch [System.Management.Automation.ItemNotFoundException] {
        return [pscustomobject]@{ KeyExists = $false; Entry = $null }
    }
    try {
        return [pscustomobject]@{
            KeyExists = $true
            Entry = $key.GetValue($entryName, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        }
    } finally {
        $key.Dispose()
    }
}

$mutex = $null
$locked = $false
$failure = 'Unable to lock the autostart registration.'
try {
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $mutex = [System.Threading.Mutex]::new($false, 'Global\OpenGloss.Autostart.' + $sid)
    try {
        $locked = $mutex.WaitOne(10000)
    } catch [System.Threading.AbandonedMutexException] {
        $locked = $true
    }
    if (-not $locked) { throw $failure }

    $failure = 'Unable to read the autostart registration.'
    $registration = Read-Registration
    $entry = $registration.Entry
    $owned = $entry -is [string] -and [string]::Equals($entry, $expected, [StringComparison]::OrdinalIgnoreCase)
    if ($null -ne $entry -and -not $owned) {
        $failure = 'Autostart is already registered to another or unrecognized installation.'
        throw $failure
    }

    if ($Remove) {
        if (-not $owned) {
            Write-Host '[PAE] Autostart is already off.'
            exit 0
        }
        $failure = 'Unable to remove the autostart registration.'
        Remove-ItemProperty -LiteralPath $runKey -Name $entryName -ErrorAction Stop
        if ($null -ne (Read-Registration).Entry) { throw $failure }
        $failure = 'Autostart is off, but the local launcher could not be removed.'
        if (Test-Path -LiteralPath $vbsPath) { Remove-Item -LiteralPath $vbsPath -Force }
        Write-Host '[PAE] Autostart is off.'
        exit 0
    }

    $failure = 'Bundled Python or the start script is missing. Run Install.cmd first.'
    foreach ($name in 'python.exe', 'start_engine.bat') {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $name) -PathType Leaf)) { throw $failure }
    }
    $failure = 'Unable to prepare the local autostart launcher.'
    $vbs = @(
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        'Dim d, sh',
        'd = fso.GetParentFolderName(WScript.ScriptFullName)',
        'Set sh = CreateObject("WScript.Shell")',
        'sh.CurrentDirectory = d',
        'sh.Run "cmd /c start_engine.bat < nul >> autostart.log 2>&1", 0, False'
    )
    if (Test-Path -LiteralPath $vbsPath) {
        $launcher = Get-Item -LiteralPath $vbsPath
        if ($launcher.PSIsContainer -or ($launcher.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw $failure }
    }
    Set-Content -LiteralPath $vbsPath -Value $vbs -Encoding ASCII

    if (-not $owned) {
        $failure = 'Unable to save the autostart registration.'
        if (-not $registration.KeyExists) { New-Item -Path $runKey | Out-Null }
        Set-ItemProperty -LiteralPath $runKey -Name $entryName -Value $expected -Type String -ErrorAction Stop
        $saved = (Read-Registration).Entry
        if ($saved -isnot [string] -or -not [string]::Equals($saved, $expected, [StringComparison]::OrdinalIgnoreCase)) {
            throw $failure
        }
    }
    Write-Host '[PAE] Autostart is on. The engine will start at the next logon.'
} catch {
    Write-Host ('[PAE] ERROR: ' + $failure)
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
exit 0
