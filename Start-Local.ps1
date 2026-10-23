# Run from PowerShell to build and pin the sandbox before starting services.
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $envPath = Join-Path $PSScriptRoot '.env'
    $settings = [IO.File]::ReadAllText($envPath)
    if ($settings -notmatch '(?m)^SANDBOX_IMAGE=') { throw 'Missing SANDBOX_IMAGE in .env' }
    docker compose build api worker sandbox-runtime
    if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    $imageId = docker image inspect oantuti-sandbox:dev --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') { throw 'Cannot resolve sandbox image' }
    docker tag $imageId ('oantuti-sandbox:pinned-' + $imageId.Substring(7))
    if ($LASTEXITCODE -ne 0) { throw 'Cannot retain sandbox image' }
    $settings = [regex]::Replace($settings, '(?m)^SANDBOX_IMAGE=[^\r\n]+', 'SANDBOX_IMAGE=' + $imageId)
    [IO.File]::WriteAllText($envPath, $settings, [Text.UTF8Encoding]::new($false))
    docker compose up -d --no-build
    if ($LASTEXITCODE -ne 0) { throw 'Starting services failed' }
    Write-Output 'Open http://localhost:8000. Keep pinned images referenced by saved bots/matches.'
} finally { Pop-Location }
