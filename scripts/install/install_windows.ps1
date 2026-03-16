param(
    [switch]$Apply,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$DocsPath = Join-Path $RepoRoot "docs/scripts/installers.md"

Write-Host "Agent Navigator Pro - Windows bootstrap"
Write-Host "Recommended path: Windows host + WSL2 Ubuntu + Docker Desktop"
Write-Host "See: $DocsPath"
Write-Host ""

$wslPresent = $false
$dockerDesktopPresent = $false
$wingetPresent = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

try {
    $null = wsl.exe --version 2>$null
    if ($LASTEXITCODE -eq 0) {
        $wslPresent = $true
    }
} catch {
    $wslPresent = $false
}

$dockerDesktopPath = Join-Path ${env:ProgramFiles} "Docker\\Docker\\Docker Desktop.exe"
if (Test-Path $dockerDesktopPath) {
    $dockerDesktopPresent = $true
}

Write-Host ("WSL installed: " + $wslPresent)
Write-Host ("Docker Desktop installed: " + $dockerDesktopPresent)
Write-Host ("winget available: " + $wingetPresent)
Write-Host ""
Write-Host "Official WSL docs: https://learn.microsoft.com/windows/wsl/install"
Write-Host ""

if ($CheckOnly -or -not $Apply) {
    Write-Host "Recommended next steps:"
    if (-not $wslPresent) {
        Write-Host "  1. Install WSL with Ubuntu: wsl --install -d Ubuntu"
    }
    if (-not $dockerDesktopPresent) {
        if ($wingetPresent) {
            Write-Host "  2. Install Docker Desktop: winget install -e --id Docker.DockerDesktop"
        } else {
            Write-Host "  2. Install Docker Desktop manually from official docs."
        }
    }
    Write-Host "  3. Open Ubuntu/WSL and run: ./scripts/launcher.sh --install --platform=wsl"
    exit 0
}

if (-not $wslPresent) {
    Write-Host "Installing WSL with Ubuntu..."
    wsl.exe --install -d Ubuntu
}

if (-not $dockerDesktopPresent) {
    if (-not $wingetPresent) {
        throw "winget is not available; install Docker Desktop manually."
    }
    Write-Host "Installing Docker Desktop..."
    winget install -e --id Docker.DockerDesktop
}

Write-Host ""
Write-Host "Windows prerequisites completed."
Write-Host "Next step inside WSL:"
Write-Host "  ./scripts/launcher.sh --install --platform=wsl"
