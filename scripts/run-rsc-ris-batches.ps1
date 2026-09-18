param(
    [Parameter(Mandatory)]
    [string]$TargetRoot,
    [Parameter(Mandatory)]
    [string]$MasterTask,
    [Parameter(Mandatory)]
    [string]$NodePath,
    [Parameter(Mandatory)]
    [string]$NodeModules,
    [ValidateRange(1, 100000)]
    [int]$Start = 201,
    [ValidateRange(1, 100000)]
    [int]$End = 1035,
    [ValidateRange(1, 1000)]
    [int]$BatchSize = 100
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$projectRoot = Split-Path -Parent $PSScriptRoot
$jobsRoot = Join-Path $projectRoot "temp\doi-harvester\jobs"
$helper = Join-Path $PSScriptRoot "ris-literature-batch.mjs"
$harvester = Join-Path $PSScriptRoot "doi-harvester.ps1"
$workbookScript = Join-Path $projectRoot ".agents\skills\autopaper-literature\scripts\literature-workbook.mjs"
$workbook = Join-Path $TargetRoot "文献检索汇总.xlsx"
$resolved = Join-Path $MasterTask "resolved-records.json"
$records = Join-Path $MasterTask "literature-records.json"

if ($End -lt $Start) {
    throw "End 不能小于 Start。"
}

for ($batchStart = $Start; $batchStart -le $End; $batchStart += $BatchSize) {
    $batchEnd = [Math]::Min($batchStart + $BatchSize - 1, $End)
    $batchName = "20260918_rsc_{0:D4}-{1:D4}" -f $batchStart, $batchEnd
    $batchRoot = Join-Path $jobsRoot $batchName
    $papers = Join-Path $batchRoot "papers.json"
    $skippedReport = Join-Path $batchRoot "subscription-skipped-report.json"
    $downloadReport = Join-Path $batchRoot "batch-report.json"
    New-Item -ItemType Directory -Force -Path $batchRoot | Out-Null

    Write-Host "[批次准备] $batchStart-$batchEnd"
    & $NodePath $helper `
        --command papers `
        --resolved $resolved `
        --records $records `
        --start $batchStart `
        --end $batchEnd `
        --exclude-journal "JOURNAL OF MATERIALS CHEMISTRY A" `
        --skipped-report $skippedReport `
        --output $papers
    if ($LASTEXITCODE -ne 0) {
        throw "生成批次 $batchStart-$batchEnd 下载清单失败。"
    }

    & $NodePath $workbookScript `
        --node-modules $NodeModules `
        --workbook $workbook `
        --report $skippedReport
    if ($LASTEXITCODE -ne 0) {
        throw "批次 $batchStart-$batchEnd 的跳过记录写回 Excel 失败。"
    }

    Write-Host "[批次下载] $batchStart-$batchEnd"
    & $harvester download `
        --papers-file $papers `
        --output-dir $TargetRoot `
        --report-dir $batchRoot `
        --browser-fallback `
        --challenge-policy pause `
        --challenge-timeout 600 `
        --delay 1.5
    $downloadExitCode = $LASTEXITCODE

    if (-not (Test-Path -LiteralPath $downloadReport)) {
        throw "批次 $batchStart-$batchEnd 未生成 batch-report.json。"
    }
    & $NodePath $workbookScript `
        --node-modules $NodeModules `
        --workbook $workbook `
        --report $downloadReport
    if ($LASTEXITCODE -ne 0) {
        throw "批次 $batchStart-$batchEnd 下载报告写回 Excel 失败。"
    }

    $report = Get-Content -LiteralPath $downloadReport -Raw | ConvertFrom-Json
    $results = @($report.results)
    $successCount = @($results | Where-Object { $_.success }).Count
    $failureCount = $results.Count - $successCount
    Write-Host "[批次完成] $batchStart-$batchEnd：成功 $successCount，失败 $failureCount，下载器退出码 $downloadExitCode"
}

Write-Host "[全部批次完成] $Start-$End"
