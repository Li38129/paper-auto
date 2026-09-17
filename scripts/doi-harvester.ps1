$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot "tmp\doi-harvester"
$developmentCache = Join-Path $runtimeRoot "dev-cache"

$env:DOI_HARVESTER_RUNTIME_DIR = $runtimeRoot
$env:UV_PROJECT_ENVIRONMENT = Join-Path $runtimeRoot "venv"
$env:UV_CACHE_DIR = Join-Path $runtimeRoot "uv-cache"
$env:PYTHONPYCACHEPREFIX = Join-Path $developmentCache "pycache"
$env:COVERAGE_FILE = Join-Path $developmentCache ".coverage"
$env:RUFF_CACHE_DIR = Join-Path $developmentCache "ruff"

New-Item -ItemType Directory -Force -Path `
    $runtimeRoot, `
    (Join-Path $runtimeRoot "jobs"), `
    (Join-Path $runtimeRoot "profiles"), `
    $developmentCache | Out-Null

& (Join-Path $PSScriptRoot "prune-runtime.ps1") -RuntimeRoot $runtimeRoot

& uv run --project $projectRoot --extra browser doi-harvester @args
$harvesterExitCode = $LASTEXITCODE

& (Join-Path $PSScriptRoot "prune-runtime.ps1") -RuntimeRoot $runtimeRoot
exit $harvesterExitCode
