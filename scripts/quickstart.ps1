# Start a safe local oc8 Community evaluation instance on Windows.
# It creates only missing local secrets and never resets containers or volumes.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Fail([string]$Message) {
  Write-Error "Quickstart stopped: $Message"
  exit 1
}

function New-HexSecret([int]$ByteCount) {
  $bytes = New-Object byte[] $ByteCount
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return ([Convert]::ToHexString($bytes)).ToLowerInvariant()
}

function New-Base64Secret([int]$ByteCount) {
  $bytes = New-Object byte[] $ByteCount
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return [Convert]::ToBase64String($bytes)
}

function Get-EnvValue([string]$Key) {
  if (-not (Test-Path ".env")) { return "" }
  $line = Get-Content ".env" | Where-Object { $_ -match "^$([regex]::Escape($Key))=" } | Select-Object -First 1
  if ($null -eq $line) { return "" }
  return $line.Substring($Key.Length + 1)
}

function Set-EnvValueIfMissing([string]$Key, [string]$Value) {
  if (-not [string]::IsNullOrWhiteSpace((Get-EnvValue $Key))) { return }
  $lines = @(Get-Content ".env")
  $pattern = "^$([regex]::Escape($Key))="
  $found = $false
  $updated = foreach ($line in $lines) {
    if ($line -match $pattern) {
      $found = $true
      "$Key=$Value"
    } else {
      $line
    }
  }
  if (-not $found) { $updated += "$Key=$Value" }
  Set-Content -Path ".env" -Value $updated -Encoding utf8
  Write-Host "Generated $Key in .env."
}

function Resolve-ContainerRuntime {
  if ($env:OC8_CONTAINER_RUNTIME) { return $env:OC8_CONTAINER_RUNTIME }
  $fromEnv = Get-EnvValue "OC8_CONTAINER_RUNTIME"
  if (-not [string]::IsNullOrWhiteSpace($fromEnv)) { return $fromEnv }
  if (Get-Command docker -ErrorAction SilentlyContinue) { return "docker" }
  if (Get-Command podman -ErrorAction SilentlyContinue) { return "podman" }
  Fail "Neither Docker nor Podman was found. Install one of them first."
}

$ContainerRuntime = Resolve-ContainerRuntime
if ($ContainerRuntime -notin @("docker", "podman")) {
  Fail "OC8_CONTAINER_RUNTIME must be 'docker' or 'podman', got '$ContainerRuntime'."
}

if ($ContainerRuntime -eq "docker") {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker Desktop is required. Install it, enable WSL2 integration if applicable, then start Docker Desktop."
  }
  try { docker compose version | Out-Null } catch { Fail "Docker Compose v2 is required." }
  try { docker info | Out-Null } catch { Fail "Docker Desktop is installed but not running." }
  $ComposeCmd = @("docker", "compose")
} else {
  if (-not (Get-Command podman -ErrorAction SilentlyContinue)) {
    Fail "Podman is required (OC8_CONTAINER_RUNTIME=podman). Install Podman Desktop first."
  }
  try {
    podman compose version | Out-Null
    $ComposeCmd = @("podman", "compose")
  } catch {
    Fail "Podman Compose is required: install the 'podman compose' plugin (Podman v4+)."
  }
  try { podman info | Out-Null } catch { Fail "Podman is installed but not ready. Run 'podman machine start' first." }
}

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example."
} else {
  Write-Host "Using existing .env; non-empty values will not be changed."
}

Set-EnvValueIfMissing "OC8_JWT_SECRET" (New-HexSecret 32)
Set-EnvValueIfMissing "OC8_SECRET_KEK" (New-Base64Secret 32)
Set-EnvValueIfMissing "POSTGRES_PASSWORD" (New-HexSecret 24)
Set-EnvValueIfMissing "OC8_SANDBOX_PROVISIONER_TOKEN" (New-HexSecret 32)
Set-EnvValueIfMissing "OC8_CONTAINER_RUNTIME" $ContainerRuntime

if ($ContainerRuntime -eq "podman") {
  $PodmanSocket = (podman info --format '{{.Host.RemoteSocket.Path}}' 2>$null)
  if ([string]::IsNullOrWhiteSpace($PodmanSocket)) {
    Fail "Could not determine the Podman API socket path (podman info --format failed)."
  }
  Set-EnvValueIfMissing "OC8_CONTAINER_SOCKET" $PodmanSocket
}

Write-Host "`nBuilding and starting oc8 Community…"
if ($ContainerRuntime -eq "docker") {
  docker compose up -d --build
} else {
  & $ComposeCmd[0] $ComposeCmd[1] up -d --build
}

$portMapping = Get-EnvValue "OC8_HTTP_PORT"
if ([string]::IsNullOrWhiteSpace($portMapping)) { $portMapping = "80" }
if ($portMapping.Contains(":")) {
  $host, $port = $portMapping -split ":", 2
  if ($host -eq "0.0.0.0" -or $host -eq "::") { $host = "127.0.0.1" }
  $url = "http://${host}:$port"
} else {
  $url = "http://localhost"
  if ($portMapping -ne "80") { $url = "$url`:$portMapping" }
}

Write-Host "`nWaiting for $url/health …"
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "$url/health"
    if ($response.StatusCode -eq 200) { $ready = $true; break }
  } catch {}
  Start-Sleep -Seconds 2
}
if (-not $ready) {
  & $ComposeCmd[0] $ComposeCmd[1] ps
  Fail "oc8 did not become healthy in time. Inspect: $($ComposeCmd -join ' ') logs -f backend"
}

Write-Host "`n✓ oc8 Community is running at $url"
Write-Host "Next: open the URL and create the local administrator account."
Write-Host "Logs: $($ComposeCmd -join ' ') logs -f backend"
Write-Host "Stop later (keeps data): $($ComposeCmd -join ' ') stop"
