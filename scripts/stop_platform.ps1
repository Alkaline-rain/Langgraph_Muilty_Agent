$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$recordPath = Join-Path $projectRoot '.platform\processes.json'
if (-not (Test-Path -LiteralPath $recordPath)) { return }
foreach ($record in (Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json)) {
    $process = Get-Process -Id $record.Id -ErrorAction SilentlyContinue
    if ($process -and $process.Path -eq $record.Exe -and $process.StartTime.ToUniversalTime().ToString('o') -eq $record.Started) {
        Stop-Process -Id $process.Id
    }
}
Write-Host 'Recorded platform processes stopped. PostgreSQL data is retained.'
