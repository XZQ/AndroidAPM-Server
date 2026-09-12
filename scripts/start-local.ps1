[CmdletBinding()]
param(
    [switch]$SkipSync,
    [switch]$SkipSeed
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$localRoot = Join-Path $repoRoot ".local"
$envPath = Join-Path $localRoot "server.env"
$databasePath = (Join-Path $localRoot "androidapm-preview.db").Replace("\", "/")

function New-Base64Secret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes)
}

function Import-LocalEnvironment {
    param([Parameter(Mandatory = $true)][string]$Path)
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $separator = $trimmed.IndexOf("=")
        if ($separator -le 0) {
            throw "Invalid local environment line: $trimmed"
        }
        $name = $trimmed.Substring(0, $separator)
        $value = $trimmed.Substring($separator + 1)
        Set-Item -Path "Env:$name" -Value $value
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it before starting the local stack."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm is required. Install it before starting the local stack."
}

New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $envPath)) {
    $installationKey = New-Base64Secret
    $cursorKey = New-Base64Secret
    $webSessionKey = New-Base64Secret
    $lines = @(
        "APM_ENVIRONMENT=local"
        "APM_LOG_LEVEL=INFO"
        "APM_DATABASE_URL=sqlite+aiosqlite:///$databasePath"
        "APM_OTLP_LOGS_ENDPOINT=http://127.0.0.1:4318/v1/logs"
        "APM_INGEST_ENABLED=true"
        "APM_EXPORT_ENABLED=false"
        "APM_INSTALLATION_HMAC_KEYS_JSON={`"v1`":`"$installationKey`"}"
        "APM_INSTALLATION_HMAC_ACTIVE_KEY_VERSION=v1"
        "APM_QUERY_CURSOR_HMAC_KEY_B64=$cursorKey"
        "APM_WEB_SESSION_HMAC_KEY_B64=$webSessionKey"
        "APM_WEB_SESSION_COOKIE_SECURE=false"
        "APM_WEB_DIST_PATH=web/dist"
        "APM_ARTIFACT_STORAGE_PATH=.local/artifacts"
        "APM_SYMBOLIZATION_ENABLED=false"
    )
    Set-Content -LiteralPath $envPath -Value $lines -Encoding utf8
    Write-Host "Created ignored local configuration: $envPath"
}

Import-LocalEnvironment -Path $envPath
Push-Location $repoRoot
try {
    if (-not $SkipSync) {
        uv sync --all-groups --frozen
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
        pnpm --dir web install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw "pnpm install failed" }
    }
    pnpm --dir web run build
    if ($LASTEXITCODE -ne 0) { throw "Web build failed" }
    uv run alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed" }
    if (-not $SkipSeed) {
        uv run python scripts/seed_local_preview.py
        if ($LASTEXITCODE -ne 0) { throw "Local fixture seeding failed" }
    }

    Write-Host ""
    Write-Host "AndroidAPM local server is starting at http://127.0.0.1:8080"
    Write-Host "The synthetic fixture is for UI verification only; Android uploads use the printed ingest key."
    Write-Host "Press Ctrl+C to stop."
    uv run androidapm-api
    if ($LASTEXITCODE -ne 0) { throw "AndroidAPM API exited with an error" }
}
finally {
    Pop-Location
}
