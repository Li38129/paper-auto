$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot "tmp\doi-harvester"
$developmentCache = Join-Path $runtimeRoot "dev-cache"
$pytestCache = Join-Path $developmentCache "pytest"

$env:DOI_HARVESTER_RUNTIME_DIR = $runtimeRoot
$env:UV_PROJECT_ENVIRONMENT = Join-Path $runtimeRoot "venv"
$env:UV_CACHE_DIR = Join-Path $runtimeRoot "uv-cache"
$env:PYTHONPYCACHEPREFIX = Join-Path $developmentCache "pycache"
$env:COVERAGE_FILE = Join-Path $developmentCache ".coverage"
$env:RUFF_CACHE_DIR = Join-Path $developmentCache "ruff"

New-Item -ItemType Directory -Force -Path `
    $runtimeRoot, `
    $developmentCache, `
    $pytestCache | Out-Null

& (Join-Path $PSScriptRoot "prune-runtime.ps1") -RuntimeRoot $runtimeRoot

& uv run --project $projectRoot --extra dev --extra browser `
    pytest -q -m "not network" -o "cache_dir=$pytestCache"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& uv run --project $projectRoot --extra dev ruff check `
    (Join-Path $projectRoot "src") `
    (Join-Path $projectRoot "tests")
exit $LASTEXITCODE
