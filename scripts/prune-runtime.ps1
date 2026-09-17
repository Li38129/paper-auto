param(
    [string]$RuntimeRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) "tmp\doi-harvester"),
    [switch]$IncludePackageCaches
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

function Remove-GeneratedDirectory {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$AllowedRoot
    )

    if (-not [System.IO.Directory]::Exists($Path)) {
        return
    }

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($AllowedRoot)
    $rootPrefix = $resolvedRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + `
        [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理运行目录之外的路径：$resolvedPath"
    }

    [System.IO.Directory]::Delete($resolvedPath, $true)
}

function Test-ProfileActive {
    param([Parameter(Mandatory)][string]$ProfileRoot)

    if (Test-Path -LiteralPath (Join-Path $ProfileRoot ".doi-harvester.lock")) {
        return $true
    }

    $statePath = Join-Path $ProfileRoot "auth-state.json"
    if (-not (Test-Path -LiteralPath $statePath)) {
        return $false
    }

    try {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if (-not $state.browser_pid) {
            return $false
        }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($state.browser_pid)" -ErrorAction SilentlyContinue
        return $null -ne $process -and $process.CommandLine -like "*$ProfileRoot*"
    }
    catch {
        return $false
    }
}

$runtimeFull = [System.IO.Path]::GetFullPath($RuntimeRoot)
$profileRoot = Join-Path $runtimeFull "profiles\default"

if (Test-ProfileActive -ProfileRoot $profileRoot) {
    Write-Host "浏览器配置正在使用，跳过缓存瘦身。"
}
else {
    $cacheDirectories = @(
        "BrowserMetrics",
        "component_crx_cache",
        "Crashpad",
        "DawnGraphiteCache",
        "GrShaderCache",
        "ShaderCache",
        "ProvenanceData",
        "ProvenanceDataTensors",
        "Safe Browsing",
        "Edge Entity Extraction",
        "EdgeLanguageDetectionModel",
        "Speech Recognition",
        "Subresource Filter",
        "Edge Shopping",
        "Edge Wallet",
        "ZxcvbnData",
        "hyphen-data",
        "Typosquatting",
        "Default\Cache",
        "Default\Code Cache",
        "Default\GPUCache",
        "Default\DawnGraphiteCache",
        "Default\DawnWebGPUCache",
        "Default\ShaderCache",
        "Default\Extensions",
        "Default\Extension Rules",
        "Default\Extension Scripts",
        "Default\Extension State",
        "Default\Local Extension Settings",
        "Default\Sync Extension Settings",
        "Default\Storage\ext",
        "Default\Service Worker\ScriptCache"
    )

    foreach ($relativePath in $cacheDirectories) {
        Remove-GeneratedDirectory `
            -Path (Join-Path $profileRoot $relativePath) `
            -AllowedRoot $profileRoot
    }
}

$jobsRoot = Join-Path $runtimeFull "jobs"
$cutoff = (Get-Date).AddDays(-30)
if (Test-Path -LiteralPath $jobsRoot) {
    foreach ($jobDirectory in Get-ChildItem -LiteralPath $jobsRoot -Directory -Force) {
        if ($jobDirectory.LastWriteTime -ge $cutoff) {
            continue
        }
        $reportPath = Join-Path $jobDirectory.FullName "batch-report.json"
        if (-not (Test-Path -LiteralPath $reportPath)) {
            continue
        }
        try {
            $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
            $results = @($report.results)
            if ($results.Count -gt 0 -and @($results | Where-Object { -not $_.success }).Count -eq 0) {
                Remove-GeneratedDirectory -Path $jobDirectory.FullName -AllowedRoot $jobsRoot
            }
        }
        catch {
            Write-Warning "无法解析任务报告，保留目录：$($jobDirectory.FullName)"
        }
    }
}

if ($IncludePackageCaches) {
    Remove-GeneratedDirectory -Path (Join-Path $runtimeFull "uv-cache") -AllowedRoot $runtimeFull
    Remove-GeneratedDirectory -Path (Join-Path $runtimeFull "dev-cache") -AllowedRoot $runtimeFull
}
