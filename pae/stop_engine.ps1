$ErrorActionPreference = 'Stop'
$port = 4815

function Get-ListeningOwners {
    Get-NetTCPConnection -ErrorAction Stop |
        Where-Object { $_.State -eq 'Listen' -and $_.LocalPort -eq $port } |
        Select-Object -ExpandProperty OwningProcess -Unique
}

$failure = 'Unable to inspect the listening port.'
try {
    $owners = @(Get-ListeningOwners)
    if ($owners.Count -eq 0) {
        Write-Host '[PAE] Engine is already stopped.'
        exit 0
    }
    $failure = 'Unable to confirm that the listener belongs to this installation.'
    if ($owners.Count -ne 1) { throw $failure }

    $ownerId = $owners[0]
    $process = Get-Process -Id $ownerId -ErrorAction Stop
    try {
        # Keep a handle to the process being verified until the operation ends.
        [void]$process.Handle
        $details = Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + $ownerId) -ErrorAction Stop
        $python = Join-Path $PSScriptRoot 'python.exe'
        $comparison = [StringComparison]::OrdinalIgnoreCase
        if (-not [string]::Equals($python, $details.ExecutablePath, $comparison) -or
            -not [string]::Equals($python, $process.MainModule.FileName, $comparison) -or
            $details.CommandLine -notmatch '^\s*(?:"[^"]+"|[^\s"]+)\s+-m\s+uvicorn\s+pae_core\.api:app(?:\s+--host\s+127\.0\.0\.1)?\s+--port\s+4815\s*$') {
            throw $failure
        }

        $currentOwners = @(Get-ListeningOwners)
        if ($currentOwners.Count -ne 1 -or $currentOwners[0] -ne $ownerId -or $process.HasExited) {
            throw $failure
        }

        $failure = 'The engine could not be stopped.'
        $process.Kill()
        $failure = 'The engine did not exit within 8 seconds.'
        if (-not $process.WaitForExit(8000)) { throw $failure }
        Write-Host '[PAE] Engine stopped.'
    } finally {
        $process.Dispose()
    }
} catch {
    Write-Host ('[PAE] ERROR: ' + $failure)
    exit 1
}
exit 0
