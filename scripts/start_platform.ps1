param([switch]$SkipDatabase)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$existingRecord = Join-Path $projectRoot '.platform\processes.json'
if (Test-Path -LiteralPath $existingRecord) {
    foreach ($record in (Get-Content -LiteralPath $existingRecord -Raw | ConvertFrom-Json)) {
        $existingProcess = Get-Process -Id $record.Id -ErrorAction SilentlyContinue
        if ($existingProcess -and $existingProcess.Path -eq $record.Exe -and $existingProcess.StartTime.ToUniversalTime().ToString('o') -eq $record.Started) {
            throw 'Platform processes already exist. Use stop_platform.ps1 before restarting.'
        }
    }
}
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not $SkipDatabase) {
    docker compose -f deploy/compose.yaml up -d --wait
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed' }
}
& $pythonExe -m backend.setup
if ($LASTEXITCODE -ne 0) { throw 'Platform initialization failed' }
$nodeExe = (Get-Command node.exe).Source
$processes = @()
$definitions = @(
    @{Name='api'; Exe=$pythonExe; Args=@('-m','uvicorn','backend.app.main:app_factory','--factory','--host','127.0.0.1','--port','8000'); Dir=$projectRoot},
    @{Name='worker'; Exe=$pythonExe; Args=@('-m','backend.worker.main'); Dir=$projectRoot},
    @{Name='frontend'; Exe=$nodeExe; Args=@('node_modules/vite/bin/vite.js','--host','127.0.0.1'); Dir=(Join-Path $projectRoot 'frontend')}
)
foreach ($service in $definitions) {
    $log = Join-Path $projectRoot ('.platform\' + $service.Name)
    $process = Start-Process -FilePath $service.Exe -ArgumentList $service.Args -WorkingDirectory $service.Dir -WindowStyle Hidden -PassThru -RedirectStandardOutput ($log + '.log') -RedirectStandardError ($log + '.error.log')
    $processes += @{Name=$service.Name; Id=$process.Id; Started=$process.StartTime.ToUniversalTime().ToString('o'); Exe=$service.Exe}
}
$processes | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot '.platform\processes.json') -Encoding UTF8
Write-Host 'Workbench: http://127.0.0.1:5173'
Write-Host 'API: http://127.0.0.1:8000/docs'
Write-Host 'Token: .platform.env; logs: .platform/; stop: scripts/stop_platform.ps1'
Write-Host 'Submitting a task uses the configured model and may incur API charges.'
