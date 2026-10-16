#Requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Image)

$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$taskOutput = Join-Path $taskRoot 'artifacts/mitigation-runner'
$taskProject = 'mitigation-runner-check-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$taskCompose = @('compose', '--env-file', (Join-Path $PSScriptRoot 'compose.defaults'),
    '-f', (Join-Path $PSScriptRoot 'compose.yaml'), '-p', $taskProject)
$taskVariables = @('API_MITIGATION_IMAGE', 'API_MITIGATION_BUILD_ID', 'FIXTURE_BROKEN')
$taskPrevious = @{}
foreach ($taskName in $taskVariables) {
    $taskPrevious[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
}

try {
    & docker image inspect $Image --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0) { throw 'Build or pull the selected generic runner image first.' }
    $env:API_MITIGATION_IMAGE = $Image
    foreach ($taskBroken in @($false, $true)) {
        $env:FIXTURE_BROKEN = $taskBroken.ToString().ToLowerInvariant()
        $taskBuildId = 'runner-' + $(if ($taskBroken) { 'reject-' } else { 'accept-' }) + [guid]::NewGuid().ToString('N')
        $env:API_MITIGATION_BUILD_ID = $taskBuildId
        & docker @taskCompose up -d --wait --wait-timeout 60 fixture
        if ($LASTEXITCODE -ne 0) { throw 'The isolated runner fixture did not become ready.' }
        & docker @taskCompose run --rm -T --no-deps --pull never runner
        $taskExit = $LASTEXITCODE
        $taskExpectedExit = if ($taskBroken) { 1 } else { 0 }
        if ($taskExit -ne $taskExpectedExit) {
            throw "Runner exit code $taskExit; expected $taskExpectedExit. Inspect $taskOutput."
        }

        $taskRun = @(Get-ChildItem -LiteralPath $taskOutput -Directory |
            Where-Object Name -Like "$taskBuildId-*")
        if ($taskRun.Count -ne 1) { throw "Expected one evidence directory for $taskBuildId." }
        foreach ($taskFile in @('verdict.json', 'evidence.sqlite', 'schemathesis.json', 'events.ndjson',
                'junit.xml', 'coverage.json', 'coverage.html', 'console.log')) {
            $taskEvidence = Join-Path $taskRun[0].FullName $taskFile
            if (!(Test-Path -LiteralPath $taskEvidence) -or (Get-Item -LiteralPath $taskEvidence).Length -eq 0) {
                throw "Missing runner evidence: $taskFile"
            }
        }
        $taskVerdict = Get-Content (Join-Path $taskRun[0].FullName 'verdict.json') -Raw | ConvertFrom-Json
        if ($taskVerdict.passed -ne (!$taskBroken) -or $taskVerdict.operations.tested -ne 1) {
            throw 'The runner verdict does not match the fixture result.'
        }
        if ($taskBroken -and @($taskVerdict.observed_failures).Count -eq 0) {
            throw 'The rejected response has no recorded check failure.'
        }
        if (!$taskBroken -and ($taskVerdict.coverage_percent.operation -ne 100 -or $taskVerdict.coverage_percent.responses -ne 100)) {
            throw 'The healthy fixture did not cover the operation and both authentication responses.'
        }
    }
    Write-Host 'Runner verified: valid response accepted; invalid response rejected; evidence retained for both.'
    Write-Host "Report: $(Join-Path $taskOutput 'report.html')"
} finally {
    & docker @taskCompose down --remove-orphans
    if ($LASTEXITCODE -ne 0) { Write-Warning "Could not clean up Compose project $taskProject." }
    foreach ($taskName in $taskVariables) {
        [Environment]::SetEnvironmentVariable($taskName, $taskPrevious[$taskName], 'Process')
    }
}
