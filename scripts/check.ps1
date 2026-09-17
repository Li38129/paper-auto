$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"

$projectRoot = Split-Path -Parent $PSScriptRoot
$tempRoot = Join-Path $projectRoot "temp"
$runtimeRoot = Join-Path $tempRoot "doi-harvester"
$legacyRuntimeRoot = Join-Path $projectRoot "tmp\doi-harvester"
$developmentCache = Join-Path $runtimeRoot "dev-cache"
$pytestCache = Join-Path $developmentCache "pytest"

# 仅在新目录尚未建立时迁移旧运行目录，避免覆盖任何现有数据。
if (
    [System.IO.Directory]::Exists($legacyRuntimeRoot) -and
    -not [System.IO.Directory]::Exists($runtimeRoot)
) {
    $legacyProfileLock = Join-Path $legacyRuntimeRoot "profiles\default\.doi-harvester.lock"
    if ([System.IO.File]::Exists($legacyProfileLock)) {
        throw "旧运行目录仍被浏览器使用，请关闭 AutoPaper 浏览器后重试迁移。"
    }
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    Move-Item -LiteralPath $legacyRuntimeRoot -Destination $runtimeRoot

    # Windows 虚拟环境含旧绝对路径，转存后由 uv 在新位置重建。
    $migratedVenv = Join-Path $runtimeRoot "venv"
    if ([System.IO.Directory]::Exists($migratedVenv)) {
        $workRoot = Join-Path $tempRoot "work"
        $staleVenv = Join-Path $workRoot ("migrated-venv-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
        New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
        Move-Item -LiteralPath $migratedVenv -Destination $staleVenv
    }
}

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
