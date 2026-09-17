param(
    [string]$NodePath = $env:AUTOPAPER_NODE,
    [string]$ArtifactNodeModules = $env:AUTOPAPER_ARTIFACT_NODE_MODULES
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot "temp\doi-harvester"
$env:DOI_HARVESTER_RUNTIME_DIR = $runtimeRoot
$env:UV_PROJECT_ENVIRONMENT = Join-Path $runtimeRoot "venv"
$env:UV_CACHE_DIR = Join-Path $runtimeRoot "uv-cache"
if ($NodePath) {
    $env:AUTOPAPER_NODE = $NodePath
}
if ($ArtifactNodeModules) {
    $env:AUTOPAPER_ARTIFACT_NODE_MODULES = $ArtifactNodeModules
}

# Artifact Tool 由 Codex 工作区运行时注入；缺失时仅 Excel 工具返回可恢复错误。
& uv run --project $projectRoot --extra agent --extra browser autopaper-mcp
exit $LASTEXITCODE
