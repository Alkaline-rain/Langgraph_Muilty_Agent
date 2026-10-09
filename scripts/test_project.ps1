param(
    [switch]$Docker,
    [switch]$BuildImage,
    [string]$BaseImage = 'python:3.12-slim',
    [string]$ProxyUrl = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Missing .venv. Run: python -m venv .venv; then install requirements.txt.'
}
Push-Location -LiteralPath $projectRoot
$previousHttpProxy = $env:HTTP_PROXY
$previousHttpsProxy = $env:HTTPS_PROXY
try {
    if ($ProxyUrl) {
        $env:HTTP_PROXY = $ProxyUrl
        $env:HTTPS_PROXY = $ProxyUrl
    }
    & $pythonExe -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed.' }
    if ($Docker -or $BuildImage) {
        $dockerExe = & $pythonExe -c "import sys; sys.path.insert(0, 'code_assistant'); from utils.docker_sandbox import find_docker; print(find_docker() or '')"
        if (-not $dockerExe) { throw 'Docker CLI not found. Install Docker Desktop and restart the terminal.' }
        & $dockerExe info --format '{{.OSType}}'
        if ($LASTEXITCODE -ne 0) { throw 'Docker engine unavailable. Start Docker Desktop first.' }
        if ($BuildImage) {
            & $dockerExe build --build-arg "PYTHON_BASE_IMAGE=$BaseImage" -t code-assistant-sandbox:offline-v1 code_assistant/sandbox_runtime
            if ($LASTEXITCODE -ne 0) { throw 'Image build failed. Check Docker Desktop proxy/DNS/network and build output.' }
        }
        & $pythonExe code_assistant/doctor.py --probe
        if ($LASTEXITCODE -ne 0) { throw 'Container smoke test failed. No LLM calls were made.' }
        & $pythonExe scripts/run_tests.py --docker
    }
    else {
        & $pythonExe scripts/run_tests.py
    }
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed. See test_results for the report.' }
}
finally {
    $env:HTTP_PROXY = $previousHttpProxy
    $env:HTTPS_PROXY = $previousHttpsProxy
    Pop-Location
}
