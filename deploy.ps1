#!/usr/bin/env pwsh
# Satori - local Docker + Cloud Build helper
#   .\deploy.ps1 build       Build the image locally (satori:dev)
#   .\deploy.ps1 run         Build + run locally on http://localhost:8080
#   .\deploy.ps1 push        Trigger a Cloud Build (uses cloudbuild.yaml)
#   .\deploy.ps1 logs        Tail the latest Cloud Build logs

param(
    [Parameter(Position=0)]
    [ValidateSet('build', 'run', 'push', 'logs', 'help')]
    [string]$Command = 'help'
)

$ErrorActionPreference = 'Stop'

$ProjectId   = $env:SATORI_GCP_PROJECT
if (-not $ProjectId) { $ProjectId = 'ai-vertex-mahad' }
$Region      = 'us-central1'
$ArRepo      = 'satori'
$ServiceName = 'satori-web'
$LocalTag    = 'satori:dev'
$RemoteImage = "${Region}-docker.pkg.dev/${ProjectId}/${ArRepo}/${ServiceName}"

function Write-Header($text) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

function Require-Cmd($cmd) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: '$cmd' not found on PATH." -ForegroundColor Red
        exit 1
    }
}

function Invoke-Build {
    Require-Cmd docker
    Write-Header "Building $LocalTag (local image)"
    docker build --tag $LocalTag .
    Write-Host "Built $LocalTag" -ForegroundColor Green
}

function Invoke-Run {
    Invoke-Build
    Write-Header "Running $LocalTag on http://localhost:8080"
    Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
    $GcloudConfig = "$env:APPDATA\gcloud"
    if (-not (Test-Path $GcloudConfig)) {
        Write-Host "WARNING: $GcloudConfig not found - run 'gcloud auth application-default login' first." -ForegroundColor Yellow
    }
    docker run --rm -it `
        -p 8080:8080 `
        -e SATORI_STATE_BACKEND=firestore `
        -e SATORI_GCP_PROJECT=$ProjectId `
        -e GOOGLE_APPLICATION_CREDENTIALS=/home/satori/.config/gcloud/application_default_credentials.json `
        -v "${GcloudConfig}:/home/satori/.config/gcloud:ro" `
        $LocalTag
}

function Invoke-Push {
    Require-Cmd gcloud
    Write-Header "Triggering Cloud Build (project=$ProjectId)"
    $sha = $null
    if (Get-Command git -ErrorAction SilentlyContinue) {
        try { $sha = (git rev-parse --short HEAD 2>$null) } catch { $sha = $null }
    }
    if ([string]::IsNullOrWhiteSpace($sha)) {
        $sha = "manual-$(Get-Date -Format yyyyMMddHHmmss)"
        Write-Host "No git SHA available - using '$sha' as tag" -ForegroundColor Yellow
    }
    gcloud builds submit `
        --project=$ProjectId `
        --config=cloudbuild.yaml `
        --substitutions=_TAG=$sha
    Write-Host ""
    Write-Host "Pushed: ${RemoteImage}:${sha}" -ForegroundColor Green
    Write-Host "        ${RemoteImage}:latest" -ForegroundColor Green
}

function Invoke-Logs {
    Require-Cmd gcloud
    Write-Header "Tailing latest Cloud Build"
    $buildId = (gcloud builds list --project=$ProjectId --limit=1 --format='value(id)')
    if (-not $buildId) {
        Write-Host "No builds found yet." -ForegroundColor Yellow
        return
    }
    gcloud builds log --project=$ProjectId --stream $buildId
}

function Show-Help {
    Write-Host ""
    Write-Host "Satori build/delivery helper" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  .\deploy.ps1 build    Build the Docker image locally"
    Write-Host "  .\deploy.ps1 run      Build, then run on http://localhost:8080"
    Write-Host "  .\deploy.ps1 push     Trigger a Cloud Build"
    Write-Host "  .\deploy.ps1 logs     Tail the most recent Cloud Build logs"
    Write-Host ""
    Write-Host "Project: $ProjectId"
    Write-Host ""
}

switch ($Command) {
    'build' { Invoke-Build }
    'run'   { Invoke-Run }
    'push'  { Invoke-Push }
    'logs'  { Invoke-Logs }
    default { Show-Help }
}
